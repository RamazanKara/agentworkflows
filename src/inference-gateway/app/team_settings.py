"""Team overrides layered on reviewed YAML; credentials and routes remain in policy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from time import time
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.audit import chain_audit_event, emit_audit_record
from app.policy import VALID_BACKENDS, ModelRoutingPolicy, SandboxPolicy
from app.teams import require_role

WORKFLOW_FIELDS = (
    "token_limit",
    "cost_limit_usd",
    "approval_required",
    "approval_threshold_usd",
    "approver_role",
    "allowed_providers",
)
APPROVER_ROLES = ("admin", "approver")

CHANGE = """
local raw = redis.call('GET', KEYS[1])
local revision = raw and cjson.decode(raw).revision or 0
if revision ~= tonumber(ARGV[1]) then return 0 end
redis.call('SET', KEYS[1], ARGV[2])
return 1
"""


class TeamSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any] = Field(min_length=1)


@dataclass
class EffectiveTeamSettings:
    team: SandboxPolicy | None
    routing: ModelRoutingPolicy
    document: dict[str, Any]
    fields: dict[str, dict[str, Any]]

    def public(self) -> dict[str, Any]:
        return {
            **{key: self.document[key] for key in ("revision", "updated_by", "updated_at")},
            "fields": self.fields,
            "routes": list(self.routing.model_ids()),
            "providers": sorted(VALID_BACKENDS),
            "approver_roles": list(APPROVER_ROLES),
        }


def settings_key(request: Request) -> str:
    return f"{request.app.state.settings.sandbox_budget_key_prefix}:team-settings:{request.state.sandbox_id}"


async def effective_team_settings(request: Request) -> EffectiveTeamSettings:
    from app.workflow_budget import redis_call

    team = request.app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
    routing = request.app.state.model_routing_policy
    document = {"revision": 0, "updated_by": None, "updated_at": None, "overrides": {}}
    # Legacy memory-backed sandboxes keep their YAML-only behavior.
    if team and request.app.state.budget_tracker.backend == "redis":
        raw = await redis_call(request, "get", settings_key(request))
        if raw:
            document = json.loads(raw)
    return layer_settings(team, routing, document)


def layer_settings(
    team: SandboxPolicy | None, routing: ModelRoutingPolicy, document: dict[str, Any]
) -> EffectiveTeamSettings:
    defaults: dict[str, Any] = {}
    if team:
        defaults["cost_limit_usd"] = team.cost_limit_usd
        defaults.update({f"project_budgets.{name}": team.project_budgets.get(name) for name in team.projects})
        for name, workflow in team.workflows.items():
            defaults.update({f"workflows.{name}.{field}": getattr(workflow, field) for field in WORKFLOW_FIELDS})
        defaults.update(
            {f"model_routes.{alias}": route.model_id for route in routing.routes for alias in route.aliases}
        )
    overrides = document["overrides"]
    fields = {
        name: {
            "value": overrides.get(name, value),
            "source": "override" if name in overrides else "policy",
            "policy_default": value,
        }
        for name, value in defaults.items()
    }
    if team:
        team = replace(
            team,
            cost_limit_usd=fields["cost_limit_usd"]["value"],
            project_budgets={name: fields[f"project_budgets.{name}"]["value"] for name in team.projects},
            workflows={
                name: workflow.model_copy(
                    update={field: fields[f"workflows.{name}.{field}"]["value"] for field in WORKFLOW_FIELDS}
                )
                for name, workflow in team.workflows.items()
            },
        )
        aliases = {
            alias: fields[f"model_routes.{alias}"]["value"] for route in routing.routes for alias in route.aliases
        }
        # Removed targets leave the alias unresolved until its override is reset.
        routing = replace(
            routing,
            routes=tuple(
                replace(route, aliases=tuple(alias for alias, model in aliases.items() if model == route.model_id))
                for route in routing.routes
            ),
        )
    return EffectiveTeamSettings(team, routing, document, fields)


def invalid_fields(errors: list[dict[str, str]]) -> None:
    raise HTTPException(
        422,
        detail={
            "reason": "team_settings_invalid",
            "message": " ".join(f"{error['field']}: {error['message']}" for error in errors),
            "fields": errors,
        },
    )


def validate_changes(settings: EffectiveTeamSettings, changes: dict[str, Any]) -> None:
    errors = []
    for field, value in changes.items():
        message = ""
        if field not in settings.fields:
            message = "Unknown setting; choose a field from GET /v1/team/settings."
        elif field.startswith("model_routes."):
            if not isinstance(value, str) or value not in settings.routing.model_ids():
                message = "Choose an existing model route ID."
        elif field.endswith(".allowed_providers"):
            if not isinstance(value, list) or any(not isinstance(v, str) or v not in VALID_BACKENDS for v in value):
                message = "Use a list of known provider names."
        elif field.endswith(".approver_role"):
            if value not in APPROVER_ROLES:
                message = "Choose an approval-capable role: admin or approver."
        elif field.endswith(".approval_required"):
            if not isinstance(value, bool):
                message = "Use true or false."
        elif field.endswith(".token_limit"):
            if type(value) is not int or not 0 <= value <= 1_000_000_000:
                message = "Use an integer between 0 and 1000000000."
        elif type(value) not in (int, float) or not 0 <= value <= 1_000_000:
            message = "Use a finite USD amount between 0 and 1000000."
        if message:
            errors.append({"field": field, "message": message})
    if errors:
        invalid_fields(errors)


def require_settings_admin(request: Request) -> dict[str, Any]:
    principal = require_role(request, "admin")
    if principal.get("project"):
        raise HTTPException(
            403,
            detail={
                "reason": "team_role_required",
                "message": "Use a team admin credential without a project restriction.",
            },
        )
    return principal


def match_revision(value: str) -> int:
    if len(value) > 22 or not re.fullmatch(r'(0|[1-9][0-9]*)|"(0|[1-9][0-9]*)"', value):
        invalid_fields([{"field": "If-Match", "message": "Use the integer revision returned by GET settings."}])
    return int(value.strip('"'))


async def change_settings(
    request: Request, response: Response, revision: int, changes: dict[str, Any], reset: str | None = None
) -> dict[str, Any]:
    from app.workflow_budget import redis_call

    principal = require_settings_admin(request)
    current = await effective_team_settings(request)
    if reset is not None:
        if reset not in current.fields:
            invalid_fields([{"field": reset, "message": "Unknown setting."}])
    else:
        validate_changes(current, changes)
    overrides = dict(current.document["overrides"])
    if reset is not None:
        overrides.pop(reset, None)
    else:
        overrides.update(changes)
    document = {
        "revision": revision + 1,
        "updated_by": principal.get("sub") or principal.get("key_id"),
        "updated_at": time(),
        "overrides": overrides,
    }
    if current.document["revision"] != revision or not await redis_call(
        request, "eval", CHANGE, 1, settings_key(request), revision, json.dumps(document, allow_nan=False)
    ):
        raise HTTPException(
            409,
            detail={
                "reason": "team_settings_conflict",
                "message": "Settings changed. Reload and review them before saving again.",
            },
        )
    after = layer_settings(
        request.app.state.sandbox_policy_set.policies.get(request.state.sandbox_id),
        request.app.state.model_routing_policy,
        document,
    )
    changed = [reset] if reset is not None else list(changes)
    event = {
        "event": "team_settings_changed",
        "action_type": "team_settings_changed",
        "chain_id": request.app.state.audit_chain_id,
        "sandbox_id": request.state.sandbox_id,
        "request_id": request.state.request_id,
        "principal": principal,
        "actor": document["updated_by"],
        "ts": document["updated_at"],
        "revision": document["revision"],
        "before": {field: current.fields[field] for field in changed},
        "after": {field: after.fields[field] for field in changed},
    }
    chain_audit_event(request, event)
    emit_audit_record(event)
    response.headers["ETag"] = f'"{document["revision"]}"'
    response.headers["Cache-Control"] = "no-store"
    return after.public()


def register_team_settings_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/team/settings", tags=["teams"], summary="Inspect effective team settings and policy defaults",
        responses={403: {"description": "Team admin role required"}},
    )
    async def get_settings(request: Request, response: Response) -> dict[str, Any]:
        require_settings_admin(request)
        settings = await effective_team_settings(request)
        response.headers["ETag"] = f'"{settings.document["revision"]}"'
        response.headers["Cache-Control"] = "no-store"
        return settings.public()

    @app.patch(
        "/v1/team/settings", tags=["teams"], summary="Override team settings at the supplied revision",
        responses={403: {"description": "Team admin role required"}, 409: {"description": "Stale settings revision"}},
    )
    async def patch_settings(
        request: Request, response: Response, body: TeamSettingsPatch,
        if_match: Annotated[str, Header(alias="If-Match", title="If-Match")],
    ) -> dict[str, Any]:
        require_settings_admin(request)
        return await change_settings(request, response, match_revision(if_match), body.fields)

    @app.delete(
        "/v1/team/settings/{field:path}", tags=["teams"], summary="Reset one override to its YAML policy default",
        responses={403: {"description": "Team admin role required"}, 409: {"description": "Stale settings revision"}},
    )
    async def reset_setting(
        request: Request, response: Response, field: str,
        if_match: Annotated[str, Header(alias="If-Match", title="If-Match")],
    ) -> dict[str, Any]:
        require_settings_admin(request)
        return await change_settings(request, response, match_revision(if_match), {}, reset=field)
