"""Run-scoped accounting in the existing budget Redis; execution belongs to Temporal."""

from __future__ import annotations

import asyncio
from decimal import ROUND_CEILING, Decimal
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError

from app.budget import actual_total_tokens, budget_delta
from app.policy import ModelRoute
from app.settings import AdmissionPolicyError, Settings


class RunBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_limit: int = Field(default=10000, gt=0, le=1_000_000_000, strict=True)
    cost_limit_usd: float = Field(default=5.0, gt=0, le=1_000_000, allow_inf_nan=False)
    workflow: str = Field(default="", max_length=128)


INIT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  if redis.call('HGET', KEYS[1], 'token_limit') ~= ARGV[1] or
     redis.call('HGET', KEYS[1], 'cost_limit') ~= ARGV[2] or
     (redis.call('HGET', KEYS[1], 'workflow') or '') ~= ARGV[3] or
     (redis.call('HGET', KEYS[1], 'policy_required') or '0') ~= ARGV[4] then return 0 end
else
  redis.call('HSET', KEYS[1], 'token_limit', ARGV[1], 'cost_limit', ARGV[2], 'tokens', 0, 'cost', 0,
             'workflow', ARGV[3], 'policy_required', ARGV[4])
end
return 1
"""

RESERVE = """
if redis.call('EXISTS', KEYS[1]) == 0 then return -1 end
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens')) + tonumber(ARGV[1])
local cost = tonumber(redis.call('HGET', KEYS[1], 'cost')) + tonumber(ARGV[2])
if tokens > tonumber(redis.call('HGET', KEYS[1], 'token_limit')) then return 1 end
if cost > tonumber(redis.call('HGET', KEYS[1], 'cost_limit')) then return 2 end
redis.call('HINCRBY', KEYS[1], 'tokens', ARGV[1])
redis.call('HINCRBY', KEYS[1], 'cost', ARGV[2])
return 0
"""

SETTLE = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
redis.call('HINCRBY', KEYS[1], 'tokens', ARGV[1])
redis.call('HINCRBY', KEYS[1], 'cost', ARGV[2])
return 1
"""


def nanodollars(value: float) -> int:
    return int((Decimal(str(value)) * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))


def run_store(request: Request) -> Any:
    tracker = request.app.state.budget_tracker
    if tracker.backend != "redis":
        raise HTTPException(
            503,
            detail={
                "reason": "workflow_store_required",
                "message": "Workflows require durable Redis: set SANDBOX_BUDGET_BACKEND=redis "
                "and enable Redis persistence.",
            },
        )
    return tracker.client


def run_key(request: Request, run_id: str | None = None) -> str:
    return (
        f"{request.app.state.settings.sandbox_budget_key_prefix}:workflow:"
        f"{request.state.sandbox_id}:{run_id or request.state.workflow_run_id}"
    )


async def redis_call(request: Request, method: str, *args: Any, **kwargs: Any) -> Any:
    try:
        return await asyncio.to_thread(getattr(run_store(request), method), *args, **kwargs)
    except (RedisError, OSError) as exc:
        raise HTTPException(
            503,
            detail={
                "reason": "workflow_store_unavailable",
                "message": "Workflow budget Redis is unavailable; retry later.",
            },
        ) from exc


async def reserve_run(request: Request, tokens: int, cost: float) -> tuple[int, int] | None:
    if not getattr(request.state, "workflow_run_id", None):
        return None
    amount = nanodollars(cost)
    result = await redis_call(request, "eval", RESERVE, 1, run_key(request), tokens, amount)
    if result == -1:
        raise AdmissionPolicyError("workflow_run_missing", "Initialize this run with PUT /v1/workflow-runs/{run_id}.")
    if result:
        dimension = "token" if result == 1 else "cost"
        raise AdmissionPolicyError(
            f"workflow_{dimension}_budget_exceeded",
            f"Workflow {dimension} budget exhausted; inspect /v1/workflow-runs/{request.state.workflow_run_id}.",
        )
    request.state.workflow_charge = {"tokens": tokens, "cost_usd": cost}
    await record_budget_threshold(request)
    return tokens, amount


async def record_budget_threshold(request: Request) -> None:
    team = request.app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
    if not team or not team.notifications:
        return
    raw = await redis_call(request, "hgetall", run_key(request))
    if any(
        int(raw[limit]) and int(raw[used]) >= int(raw[limit]) * team.notifications.budget_threshold
        for used, limit in (("tokens", "token_limit"), ("cost", "cost_limit"))
    ):
        from app.workflow_notifications import queue_event

        # Reservations can settle below the threshold before the run monitor next polls.
        await queue_event(request, request.state.workflow_run_id, "budget_threshold")


async def load_run_policy(request: Request) -> None:
    if not getattr(request.state, "workflow_run_id", None):
        return
    raw = await redis_call(request, "hgetall", run_key(request))
    if not raw:
        raise AdmissionPolicyError("workflow_run_missing", "Initialize this run with PUT /v1/workflow-runs/{run_id}.")
    name = raw.get("workflow", "")
    request.state.workflow_name = name
    team = request.app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
    if team and team.projects:
        request.state.project_id = raw.get("project") or team.projects[0]
        if (request.state.principal or {}).get("auth") != "workflow_step":
            from app.teams import project_access

            project_access(request, request.state.project_id)
    policy = team.workflows.get(name) if team else None
    if policy is None and (raw.get("policy_required") == "1" or (team and team.workflows)):
        raise AdmissionPolicyError("workflow_not_allowed", "Choose a workflow from GET /v1/workflow-policies.")
    request.state.workflow_policy = policy


def model_charge(settings: Settings, route: ModelRoute, payload: dict[str, Any]) -> tuple[int, float]:
    tokens = budget_delta(settings, payload).estimated_tokens
    prices = (route.input_usd_per_1k_tokens, route.output_usd_per_1k_tokens)
    if None in prices:
        raise AdmissionPolicyError(
            "workflow_price_missing", f"Configure input and output prices for model {route.model_id}."
        )
    # The more expensive token price also covers reasoning tokens and unknown input/output splits.
    return tokens, tokens * max(float(price) for price in prices if price is not None) / 1000


async def settle_run_model(
    request: Request, reservation: tuple[int, int] | None, route: ModelRoute, response: dict[str, Any]
) -> None:
    if reservation is None:
        return
    usage = response.get("usage") or {}
    total = actual_total_tokens(usage)
    if total is None:
        return
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if isinstance(prompt, int) and isinstance(completion, int) and prompt + completion == total:
        cost = (
            prompt * (route.input_usd_per_1k_tokens or 0) + completion * (route.output_usd_per_1k_tokens or 0)
        ) / 1000
    else:
        cost = total * max(route.input_usd_per_1k_tokens or 0, route.output_usd_per_1k_tokens or 0) / 1000
    await redis_call(
        request, "eval", SETTLE, 1, run_key(request), total - reservation[0], nanodollars(cost) - reservation[1]
    )
    request.state.workflow_charge = {"tokens": total, "cost_usd": cost}
    await record_budget_threshold(request)
