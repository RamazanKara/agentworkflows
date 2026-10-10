"""Team roles layer onto verified sandbox identities; headers never grant membership."""

import os
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from app.oidc_access import TeamSSO


class TeamTelemetry(BaseModel):
    traces_enabled: bool
    metrics_enabled: bool
    protocol: str
    service_name: str


def require_role(request: Request, *roles: str) -> dict[str, Any]:
    principal = request.state.principal or {}
    if not request.state.sandbox_bound or principal.get("role") not in roles:
        raise HTTPException(
            403,
            detail={
                "reason": "team_role_required",
                "message": f"Use a team-bound credential with role {' or '.join(roles)}; ask your team administrator.",
            },
        )
    return principal


def project_access(request: Request, project: str | None = None) -> str:
    principal = require_role(request, "admin", "builder", "approver", "viewer")
    team = request.app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
    projects = team.projects if team else ()
    bound = principal.get("project")
    selected = project or bound or (projects[0] if projects else "default")
    if selected not in projects or (bound and selected != bound):
        raise HTTPException(
            404,
            detail={
                "reason": "project_not_found",
                "message": "Choose a project available to your credential from GET /v1/team.",
            },
        )
    return selected


def authorize_team_request(request: Request) -> None:
    if not request.url.path.startswith("/v1/"):
        return
    if request.url.path in {"/v1/team/settings", "/v1/team/audit"} or request.url.path.startswith(
        ("/v1/team/settings/", "/v1/team/audit/")
    ):
        require_role(request, "admin")
    principal = request.state.principal or {}
    if principal.get("auth") == "workflow_step":
        return
    team = request.app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
    if not principal.get("role") and not (team and team.projects):
        return
    require_role(request, "admin", "builder", "approver", "viewer")
    if principal.get("project"):
        project_access(request)
    if request.method not in {"GET", "HEAD"}:
        roles = ("admin", "approver") if request.url.path.endswith("/approve") else ("admin", "builder")
        require_role(request, *roles)
    if (
        getattr(request.state, "workflow_run_id", None)
        or (request.method == "PUT" and request.url.path.startswith("/v1/workflow-runs/"))
    ) and "workflows:execute" not in principal.get("scopes", []):
        raise HTTPException(
            403,
            detail={
                "reason": "workflow_worker_required",
                "message": "Use the team worker credential for activities; start runs with POST /v1/workflow-runs.",
            },
        )


def register_team_routes(app: FastAPI) -> None:
    from app.alert_rules import register_alert_routes
    from app.audit_view import register_audit_routes
    from app.deployment import register_deployment_routes
    from app.onboarding import register_onboarding_routes
    from app.team_data import register_team_data_routes
    from app.team_settings import effective_team_settings, register_team_settings_routes
    from app.team_spend import register_spend_routes
    from app.team_workflows import register_team_workflow_routes

    register_deployment_routes(app)
    register_alert_routes(app)
    register_onboarding_routes(app)
    register_audit_routes(app)
    register_team_settings_routes(app)
    register_team_workflow_routes(app)
    register_spend_routes(app)
    register_team_data_routes(app)

    @app.get("/v1/team/telemetry", tags=["observability"], response_model=TeamTelemetry,
             summary="Inspect OTLP export configuration (team admin)")
    async def telemetry(request: Request) -> dict[str, Any]:
        from app.team_settings import require_settings_admin

        require_settings_admin(request)
        settings = app.state.settings
        return {"traces_enabled": settings.otel_tracing_enabled, "metrics_enabled": settings.otel_metrics_enabled,
                "protocol": "http/protobuf", "service_name": settings.otel_service_name}

    @app.get("/v1/team/sso", tags=["teams"], response_model=TeamSSO,
             summary="Inspect this team's company sign-in policy (unrestricted admin only)")
    async def team_sso(request: Request) -> dict[str, Any]:
        principal = require_role(request, "admin")
        if principal.get("project"):
            raise HTTPException(403, detail="Use an unrestricted team admin credential to inspect company sign-in.")
        settings = app.state.settings
        groups = bool(settings.oidc_group_role_mappings)
        return {
            "team_id": request.state.sandbox_id,
            "enabled": bool(settings.oidc_issuer),
            "provider_name": urlsplit(settings.oidc_issuer).hostname,
            "role_source": "groups" if groups else "claim",
            "team_claim": settings.oidc_team_claim or settings.jwt_tenant_claim,
            "project_claim": settings.oidc_project_claim,
            "role_claim": None if groups else settings.oidc_role_claim,
            "default_role": None if groups else settings.oidc_default_role,
            "groups_claim": settings.oidc_groups_claim if groups else None,
            "group_role_mappings": settings.oidc_group_role_mappings.get(request.state.sandbox_id, {}),
        }

    @app.get("/v1/team", tags=["teams"], summary="Discover your team, role, projects, and provider configuration")
    async def team_info(request: Request) -> dict[str, Any]:
        principal = require_role(request, "admin", "builder", "approver", "viewer")
        settings = await effective_team_settings(request)
        team = settings.team
        projects = list(team.projects) if team else []
        if principal.get("project"):
            projects = [project_access(request)]
        from app.workflow_notifications import channels

        return {
            "team_id": request.state.sandbox_id,
            "role": principal["role"],
            "projects": projects,
            "providers": sorted(team.provider_credentials) if team else [],
            "cost_limit_usd": team.cost_limit_usd if team else None,
            "soft_cost_limit_usd": team.soft_cost_limit_usd if team else None,
            "capture_content": team.capture_content if team else "none",
            "project_budgets": {name: team.project_budgets.get(name) for name in projects} if team else {},
            "model_routes": {alias: route.model_id for route in settings.routing.routes for alias in route.aliases},
            "notifications": {
                "channels": channels(team.notifications),
                "budget_threshold": team.notifications.budget_threshold,
            }
            if team and team.notifications
            else None,
            **(
                {
                    "provider_configuration": {
                        provider: {
                            "environment_variable": variable,
                            "configured": bool(
                                os.getenv(variable) or settings.document.get("provider_keys", {}).get(provider)
                            ),
                        }
                        for provider, variable in team.provider_credentials.items()
                    }
                }
                if team and principal["role"] == "admin"
                else {}
            ),
        }
