"""Team roles layer onto verified sandbox identities; headers never grant membership."""

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request


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
    @app.get("/v1/team", tags=["teams"], summary="Discover your team, role, projects, and provider configuration")
    async def team_info(request: Request) -> dict[str, Any]:
        principal = require_role(request, "admin", "builder", "approver", "viewer")
        team = app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
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
            "notifications": {
                "channels": channels(team.notifications),
                "budget_threshold": team.notifications.budget_threshold,
            }
            if team and team.notifications
            else None,
            **(
                {
                    "provider_configuration": {
                        provider: {"environment_variable": variable, "configured": bool(os.getenv(variable))}
                        for provider, variable in team.provider_credentials.items()
                    }
                }
                if team and principal["role"] == "admin"
                else {}
            ),
        }
