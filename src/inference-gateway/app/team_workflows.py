"""Team-registered workflow types that can narrow, but never widen, the operator's approved envelope."""

import json
import re
from time import time
from typing import Annotated, Any, Literal, NoReturn
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.policy import VALID_BACKENDS, ModelRoute, SandboxPolicy, WorkflowPolicy
from app.team_settings import (
    effective_team_settings,
    match_revision,
    registered_policies,
    require_settings_admin,
    save_team_document,
)
from app.workflow_schema import InputSchema
from app.workflow_templates import TEMPLATES

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
MAX_WORKFLOWS = 50
MAX_SCHEMA_BYTES = 20_000
# Ceilings for a team without its own limits; operator YAML can still grant more.
TOKEN_CEILING = 5_000_000
COST_CEILING = 100.0


class WorkflowRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_models: list[str] = Field(min_length=1, max_length=50)
    allowed_providers: list[str] | None = Field(
        default=None, max_length=10, description="Defaults to the providers serving the chosen models."
    )
    allowed_tools: list[str] = Field(default_factory=list, max_length=50)
    token_limit: int = Field(default=10000, ge=0, le=1_000_000_000, strict=True)
    cost_limit_usd: float = Field(default=5, ge=0, le=1_000_000, allow_inf_nan=False)
    approval_required: bool = Field(default=True, strict=True)
    approver_role: Literal["admin", "approver"] = "approver"
    required_approvals: int = Field(default=1, ge=1, le=10, strict=True)
    approval_timeout_seconds: int = Field(default=604800, ge=60, le=604800, strict=True)
    input_schema: dict[str, Any] | None = Field(
        default=None, description="Optional flat JSON Schema used for validation and the console run form."
    )


class RegisteredWorkflow(BaseModel):
    name: str
    allowed_models: list[str]
    allowed_providers: list[str]
    allowed_tools: list[str]
    allowed_egress: list[str]
    token_limit: int
    cost_limit_usd: float
    approval_required: bool
    approver_role: str
    required_approvals: int
    approval_timeout_seconds: int
    input_schema: dict[str, Any] | None
    registered_by: str | None
    registered_at: float | None


class WorkflowModelOption(BaseModel):
    id: str
    provider: str
    simulated: bool


class WorkflowOptions(BaseModel):
    providers: list[str]
    models: list[WorkflowModelOption]
    tools: list[str]


class WorkflowLimits(BaseModel):
    token_limit: int
    cost_limit_usd: float


class TeamWorkflows(BaseModel):
    revision: int
    enabled: bool
    workflows: list[RegisteredWorkflow]
    reserved: list[str]
    options: WorkflowOptions
    limits: WorkflowLimits


class RegistrationResult(BaseModel):
    revision: int
    workflow: RegisteredWorkflow


class RemovalResult(BaseModel):
    revision: int
    removed: str


def invalid(errors: list[dict[str, str]]) -> NoReturn:
    raise HTTPException(
        422,
        detail={
            "reason": "team_workflow_invalid",
            "message": " ".join(f"{error['field']}: {error['message']}" for error in errors),
            "fields": errors,
        },
    )


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def route_origin(request: Request, route: ModelRoute) -> str:
    settings = request.app.state.settings
    return origin(route.base_url or (settings.ollama_base_url if route.backend == "ollama" else settings.vllm_base_url))


def permitted_routes(team: SandboxPolicy, routes: tuple[ModelRoute, ...]) -> list[ModelRoute]:
    return [route for route in routes if not team.allowed_models or route.model_id in team.allowed_models]


def limits(team: SandboxPolicy) -> dict[str, float | int]:
    cost = COST_CEILING if team.cost_limit_usd is None else min(team.cost_limit_usd, COST_CEILING)
    tokens = TOKEN_CEILING
    if team.estimated_token_budget is not None:
        tokens = min(team.estimated_token_budget, TOKEN_CEILING)
    return {"token_limit": tokens, "cost_limit_usd": cost}


def reserved_names(operator: SandboxPolicy) -> set[str]:
    return set(operator.workflows) | {workflow for workflow, _, _ in TEMPLATES.values()}


def public(name: str, row: dict[str, Any]) -> dict[str, Any]:
    policy = WorkflowPolicy.model_validate(row["policy"])
    return {
        "name": name,
        "allowed_models": policy.allowed_models,
        "allowed_providers": policy.allowed_providers,
        "allowed_tools": policy.allowed_tools,
        "allowed_egress": policy.allowed_egress,
        "token_limit": policy.token_limit,
        "cost_limit_usd": policy.cost_limit_usd,
        "approval_required": policy.approval_required,
        "approver_role": policy.approver_role,
        "required_approvals": policy.required_approvals,
        "approval_timeout_seconds": policy.approval_timeout_seconds,
        "input_schema": policy.model_dump(by_alias=True)["inputSchema"],
        "registered_by": row.get("registered_by"),
        "registered_at": row.get("registered_at"),
    }


def build_policy(
    request: Request, team: SandboxPolicy, routes: tuple[ModelRoute, ...], body: WorkflowRegistration
) -> WorkflowPolicy:
    """Validate against what the operator already approved for this team; derive egress from it."""
    errors: list[dict[str, str]] = []
    available = {route.model_id: route for route in permitted_routes(team, routes)}
    chosen = []
    for model in dict.fromkeys(body.allowed_models):
        if model not in available:
            errors.append({"field": "allowed_models", "message": f"{model} is not an approved model for this team."})
        else:
            chosen.append(available[model])
    derived = sorted({route.backend for route in chosen})
    providers = list(dict.fromkeys(body.allowed_providers)) if body.allowed_providers is not None else derived
    unknown = sorted(set(providers) - {route.backend for route in available.values()})
    if unknown or set(providers) - VALID_BACKENDS:
        errors.append({"field": "allowed_providers", "message": "Choose providers that serve this team's models."})
    elif not providers or set(derived) - set(providers):
        errors.append({"field": "allowed_providers", "message": "Include the provider of every chosen model."})
    tools = list(dict.fromkeys(body.allowed_tools))
    for tool in tools:
        if tool not in team.tools:
            errors.append({"field": "allowed_tools", "message": f"{tool} is not a tool approved for this team."})
    ceiling = limits(team)
    if body.token_limit > ceiling["token_limit"]:
        errors.append({"field": "token_limit", "message": f"Use at most {ceiling['token_limit']:,} tokens."})
    if body.cost_limit_usd > ceiling["cost_limit_usd"]:
        errors.append({"field": "cost_limit_usd", "message": f"Use at most ${ceiling['cost_limit_usd']:g}."})
    schema = None
    if body.input_schema is not None:
        if len(json.dumps(body.input_schema)) > MAX_SCHEMA_BYTES:
            errors.append({"field": "input_schema", "message": "Keep the schema under 20 KB."})
        else:
            try:
                schema = InputSchema.model_validate(body.input_schema).model_dump(by_alias=True, exclude_unset=True)
            except ValidationError as exc:
                detail = exc.errors()[0]
                where = ".".join(str(part) for part in detail["loc"])
                errors.append({"field": "input_schema", "message": f"{where}: {detail['msg']}".strip(": ")})
    if errors:
        invalid(errors)
    egress = sorted(
        {route_origin(request, route) for route in chosen} | {origin(str(team.tools[tool].url)) for tool in tools}
    )
    try:
        return WorkflowPolicy.model_validate(
            {
                "allowedProviders": providers,
                "allowedModels": [route.model_id for route in chosen],
                "allowedTools": tools,
                "allowedEgress": egress,
                "tokenLimit": body.token_limit,
                "costLimitUsd": body.cost_limit_usd,
                "approvalRequired": body.approval_required,
                "approverRole": body.approver_role,
                "requiredApprovals": body.required_approvals,
                "approvalTimeoutSeconds": body.approval_timeout_seconds,
                **({"inputSchema": schema} if schema is not None else {}),
            }
        )
    except ValidationError as exc:
        invalid([{"field": "workflow", "message": exc.errors()[0]["msg"]}])


def operator_team(request: Request) -> SandboxPolicy:
    team = request.app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
    if team is None:
        raise HTTPException(404, detail="This credential's team has no workflow policy.")
    return team


def require_enabled(team: SandboxPolicy) -> None:
    if not team.self_service_workflows:
        raise HTTPException(
            403,
            detail={
                "reason": "team_workflows_disabled",
                "message": "Your operator keeps workflow types in reviewed policy. Ask them to add this workflow.",
            },
        )


def register_team_workflow_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/team/workflows",
        tags=["workflows"],
        response_model=TeamWorkflows,
        summary="List team-registered workflows and what a registration may use (team admin)",
    )
    async def get_team_workflows(request: Request, response: Response) -> dict[str, Any]:
        require_settings_admin(request)
        team = operator_team(request)
        settings = await effective_team_settings(request)
        routes = permitted_routes(team, settings.routing.routes)
        registered = registered_policies(team, settings.document)
        response.headers["Cache-Control"] = "no-store"
        return {
            "revision": settings.document["revision"],
            "enabled": team.self_service_workflows,
            "workflows": [
                public(name, row)
                for name, row in (settings.document.get("team_workflows") or {}).items()
                if name in registered
            ],
            "reserved": sorted(reserved_names(team)),
            "options": {
                "providers": sorted({route.backend for route in routes}),
                "models": [
                    {"id": route.model_id, "provider": route.backend, "simulated": route.simulated} for route in routes
                ],
                "tools": sorted(team.tools),
            },
            "limits": limits(team),
        }

    @app.put(
        "/v1/team/workflows/{name}",
        tags=["workflows"],
        response_model=RegistrationResult,
        summary="Register or replace a workflow type inside the models, tools and limits the team already has",
    )
    async def put_team_workflow(
        request: Request,
        response: Response,
        name: str,
        body: WorkflowRegistration,
        if_match: Annotated[str, Header(alias="If-Match")],
    ) -> dict[str, Any]:
        principal = require_settings_admin(request)
        team = operator_team(request)
        require_enabled(team)
        settings = await effective_team_settings(request)
        revision = match_revision(if_match)
        if revision != settings.document["revision"]:
            raise HTTPException(
                409,
                detail={
                    "reason": "team_workflows_conflict",
                    "message": "Team settings changed. Reload your workflows before saving again.",
                },
            )
        if not NAME.fullmatch(name):
            invalid([{"field": "name", "message": "Use 1-64 letters, digits or underscores, starting with a letter."}])
        if name in reserved_names(team):
            invalid([{"field": "name", "message": "This name is defined by your operator or a built-in template."}])
        registrations = dict(settings.document.get("team_workflows") or {})
        if name not in registrations and len(registrations) >= MAX_WORKFLOWS:
            invalid([{"field": "name", "message": f"A team can register at most {MAX_WORKFLOWS} workflows."}])
        policy = build_policy(request, team, settings.routing.routes, body)
        dump = policy.model_dump(by_alias=True, exclude_unset=True, mode="json")
        existing = registrations.get(name)
        if existing and existing.get("policy") == dump:
            row = existing
            saved_revision = settings.document["revision"]
        else:
            row = {
                "policy": dump,
                "registered_by": principal.get("sub") or principal.get("key_id"),
                "registered_at": time(),
            }
            registrations[name] = row
            saved = await save_team_document(
                request,
                settings.document,
                {"team_workflows": registrations},
                "team_workflow_registered",
                workflow=name,
                policy=dump,
                replaced=existing is not None,
            )
            saved_revision = saved["revision"]
        response.headers["ETag"] = f'"{saved_revision}"'
        response.headers["Cache-Control"] = "no-store"
        return {"revision": saved_revision, "workflow": public(name, row)}

    @app.delete(
        "/v1/team/workflows/{name}",
        tags=["workflows"],
        response_model=RemovalResult,
        summary="Remove a team-registered workflow type; new calls for it are denied",
    )
    async def delete_team_workflow(
        request: Request,
        response: Response,
        name: str,
        if_match: Annotated[str, Header(alias="If-Match")],
    ) -> dict[str, Any]:
        require_settings_admin(request)
        team = operator_team(request)
        require_enabled(team)
        settings = await effective_team_settings(request)
        if match_revision(if_match) != settings.document["revision"]:
            raise HTTPException(
                409,
                detail={
                    "reason": "team_workflows_conflict",
                    "message": "Team settings changed. Reload your workflows before removing one.",
                },
            )
        registrations = dict(settings.document.get("team_workflows") or {})
        if name not in registrations:
            raise HTTPException(404, detail="This team has not registered that workflow.")
        del registrations[name]
        prefix = f"workflows.{name}."
        overrides = {key: value for key, value in settings.document["overrides"].items() if not key.startswith(prefix)}
        saved = await save_team_document(
            request,
            settings.document,
            {"team_workflows": registrations, "overrides": overrides},
            "team_workflow_removed",
            workflow=name,
        )
        response.headers["ETag"] = f'"{saved["revision"]}"'
        response.headers["Cache-Control"] = "no-store"
        return {"revision": saved["revision"], "removed": name}

