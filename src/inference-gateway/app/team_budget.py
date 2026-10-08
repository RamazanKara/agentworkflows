"""Atomic cross-provider spend reservations in the existing budget Redis."""

from calendar import monthrange
from datetime import UTC, datetime
from typing import Any

from fastapi import Request

from app.budget import actual_total_tokens
from app.settings import AdmissionPolicyError
from app.team_settings import effective_team_settings
from app.workflow_budget import model_charge, nanodollars, redis_call

RESERVE_COST = """
local current = tonumber(redis.call('HGET', KEYS[1], 'cost') or '0')
if tonumber(ARGV[2]) >= 0 and current + tonumber(ARGV[1]) > tonumber(ARGV[2]) then return 0 end
local project = tonumber(redis.call('HGET', KEYS[1], ARGV[3]) or '0')
if tonumber(ARGV[4]) >= 0 and project + tonumber(ARGV[1]) > tonumber(ARGV[4]) then return -1 end
redis.call('HINCRBY', KEYS[1], 'cost', ARGV[1])
redis.call('HINCRBY', KEYS[1], ARGV[3], ARGV[1])
return 1
"""

SETTLE_COST = """
redis.call('HINCRBY', KEYS[1], 'cost', ARGV[1])
for i = 2, #ARGV, 2 do redis.call('HINCRBY', KEYS[1], ARGV[i], ARGV[i+1]) end
return 1
"""


def cost_key(request: Request) -> tuple[str, int]:
    settings = request.app.state.settings
    now = datetime.now(UTC)
    start = int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    return f"{settings.sandbox_budget_key_prefix}:{request.state.sandbox_id}:cost:month:{start}", start


async def reserve_team_cost(request: Request, payload: dict[str, Any]) -> None:
    from app.governance import effective_settings, route_settings

    team = (await effective_team_settings(request)).team
    if not team or (not team.projects and team.cost_limit_usd is None):
        return
    routes = getattr(request.state, "cost_routes", [])
    settings = effective_settings(request, request.app.state.sandbox_policy_set, request.app.state.settings)
    charges = []
    for route in routes:
        tokens, cost = model_charge(route_settings(settings, route), route, payload)
        charges.append(
            {"provider": route.backend, "model": route.model_id, "tokens": tokens, "cost": nanodollars(cost)}
        )
    tool = getattr(request.state, "tool_cost", None)
    if tool is not None:
        charges.append({"provider": "tool", "model": "", "tokens": 0, "cost": nanodollars(tool)})
    if not charges:
        return
    key, _ = cost_key(request)
    amount = sum(charge["cost"] for charge in charges)
    project = (
        getattr(request.state, "project_id", None)
        or (request.state.principal or {}).get("project")
        or (team.projects[0] if team.projects else "default")
    )
    project_limit = team.project_budgets.get(project)
    result = await redis_call(
        request, "eval", RESERVE_COST, 1, key, amount,
        nanodollars(team.cost_limit_usd) if team.cost_limit_usd is not None else -1,
        f"project.{project}.cost", nanodollars(project_limit) if project_limit is not None else -1,
    )
    if result != 1:
        dimension = "project" if result == -1 else "team"
        raise AdmissionPolicyError(
            f"{dimension}_cost_budget_exceeded",
            f"Monthly {dimension} spend limit reached across providers; inspect GET /v1/usage "
            "or ask your administrator to update team settings.",
        )
    request.state.team_cost_reservation = (key, amount, charges, project)


async def settle_team_cost(request: Request, response: dict[str, Any] | None) -> None:
    reservation = getattr(request.state, "team_cost_reservation", None)
    if reservation is None:
        return
    request.state.team_cost_reservation = None
    key, reserved, charges, project = reservation
    attempts = getattr(request.state, "routing_attempts", [])
    attempted = {a["model"] for a in attempts if a.get("status") in {"served", "failed"}}
    selected = getattr(request.state, "selected_route", None)
    usage = (response or {}).get("usage")
    total = actual_total_tokens(usage)
    args: list[Any] = []
    charged = 0
    for charge in charges:
        if attempts and charge["model"] not in attempted:
            continue
        if charge["provider"] == "tool" and not getattr(request.state, "workflow_charge", None):
            continue
        amount, tokens = charge["cost"], charge["tokens"]
        if selected and charge["model"] == selected.model_id and total is not None:
            amount = nanodollars(getattr(request.state, "usage_cost", 0) or 0)
            tokens = total
        charged += amount
        bases = [f"provider.{charge['provider']}", f"project.{project}.provider.{charge['provider']}"]
        workflow = getattr(request.state, "workflow_name", None)
        if workflow:
            bases.extend((f"workflow.{workflow}", f"project.{project}.workflow.{workflow}"))
        for base in bases:
            args.extend((f"{base}.cost", amount, f"{base}.tokens", tokens, f"{base}.calls", 1))
    await redis_call(
        request, "eval", SETTLE_COST, 1, key, charged - reserved,
        f"project.{project}.cost", charged - reserved, *args,
    )
    request.state.team_cost_usd = charged / 1_000_000_000


async def team_cost_report(request: Request) -> dict[str, Any]:
    team = (await effective_team_settings(request)).team
    if not team or (not team.projects and team.cost_limit_usd is None):
        return {}
    key, start = cost_key(request)
    raw = await redis_call(request, "hgetall", key)
    project = (request.state.principal or {}).get("project")
    groups: dict[str, dict[str, Any]] = {"providers": {}, "workflows": {}}
    for dimension, rows in groups.items():
        prefix = f"project.{project}." if project else ""
        prefix += dimension[:-1] + "."
        for name, value in raw.items():
            if name.startswith(prefix):
                group, field = name[len(prefix) :].rsplit(".", 1)
                rows.setdefault(group, {})["cost_usd" if field == "cost" else field] = (
                    int(value) / 1_000_000_000 if field == "cost" else int(value)
                )
    return {
        "team_id": request.state.sandbox_id,
        "project": project,
        "window_start": start,
        "window_seconds": monthrange(datetime.fromtimestamp(start, UTC).year,
                                     datetime.fromtimestamp(start, UTC).month)[1] * 86400,
        "period": "month",
        "cost_limit_usd": team.cost_limit_usd,
        "project_budgets": {
            name: {"cost_limit_usd": team.project_budgets.get(name),
                   "reserved_and_spent_usd": int(raw.get(f"project.{name}.cost", 0)) / 1_000_000_000}
            for name in team.projects if not project or name == project
        },
        "reserved_and_spent_usd": None if project else int(raw.get("cost", 0)) / 1_000_000_000,
        **groups,
        "accounting": "Configured prices; unreported calls retain conservative reservations. Not a provider invoice.",
    }
