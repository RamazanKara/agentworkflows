"""Inspect deployment configuration without exposing operator credentials."""

from fastapi import FastAPI, Request
from pydantic import BaseModel

from app.team_settings import require_settings_admin


class DeploymentCheck(BaseModel):
    id: str
    name: str
    configured: bool
    action: str


class DeploymentReadiness(BaseModel):
    checks: list[DeploymentCheck]
    verification_required: list[str]


def register_deployment_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/team/deployment",
        tags=["teams"],
        response_model=DeploymentReadiness,
        summary="Inspect deployment configuration (unrestricted admin only)",
    )
    async def deployment(request: Request) -> DeploymentReadiness:
        require_settings_admin(request)
        settings = app.state.settings
        checks = [
            (
                "authentication",
                "Team authentication",
                settings.api_key_auth_enabled or settings.jwt_auth_enabled,
                "Enable team-bound API credentials before exposing the gateway.",
            ),
            (
                "cookies",
                "Secure session cookies",
                settings.session_cookie_secure,
                "Terminate HTTPS at the ingress and enable Secure session cookies.",
            ),
            (
                "sso",
                "Company sign-in",
                bool(settings.oidc_issuer),
                "Register the HTTPS callback and map company groups to team roles.",
            ),
            (
                "encryption",
                "Secret encryption",
                bool(settings.workflow_secrets_key),
                "Configure a persistent encryption key and back it up separately from encrypted records.",
            ),
            (
                "accounting",
                "Shared accounting",
                settings.sandbox_budget_backend == "redis",
                "Configure authenticated Redis for shared budgets, sessions and invitations.",
            ),
            (
                "records",
                "PostgreSQL records",
                settings.storage_backend == "postgres",
                "Use managed PostgreSQL for gateway records and test restoring its backup.",
            ),
            (
                "audit",
                "Shared audit chain",
                settings.audit_chain_store_backend == "redis",
                "Persist the audit chain in Redis and export receipts to independent storage.",
            ),
        ]
        return DeploymentReadiness(
            checks=[
                DeploymentCheck(id=id, name=name, configured=bool(configured), action=action)
                for id, name, configured, action in checks
            ],
            verification_required=[
                "Verify HTTPS, database TLS, approved network egress and secret rotation.",
                "Restore PostgreSQL, Redis, Temporal and encryption keys in an isolated environment.",
                "Run agentworkflows check after every install and upgrade to prove a governed run completes.",
            ],
        )
