"""Team-scoped setup checks and encrypted provider credential provisioning."""

import asyncio
import json
import os
from time import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.policy import CLOUD_BACKENDS
from app.settings import AdmissionPolicyError
from app.team_settings import effective_team_settings, require_settings_admin, save_team_document
from app.teams import require_role
from app.workflow_secrets import cipher, metadata
from app.workflow_templates import TEMPLATE_VERSION


class ProviderKeyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: SecretStr
    expected_version: int = Field(ge=0, strict=True)


class ProviderSetup(BaseModel):
    provider: str
    configured: bool
    version: int
    can_save: bool


class SampleSetup(BaseModel):
    template_id: str
    version: str
    workflow: str
    installed: bool
    input: dict[str, Any]
    ready: bool


class Onboarding(BaseModel):
    providers: list[ProviderSetup]
    sample: SampleSetup | None
    blockers: list[str]


async def saved_provider_key(storage: Any, settings: Any, team_id: str, provider: str) -> str | None:
    document = await asyncio.to_thread(storage.get_settings, team_id)
    record = (document or {}).get("provider_keys", {}).get(provider)
    if not record:
        return None
    try:
        value = json.loads(Fernet(settings.workflow_secrets_key.encode()).decrypt(record["ciphertext"].encode()))
    except (InvalidToken, ValueError) as exc:
        raise AdmissionPolicyError("provider_not_configured", "Stored provider key cannot be decrypted.") from exc
    if (value["team"], value["provider"]) != (team_id, provider):
        raise AdmissionPolicyError("provider_not_configured", "Stored provider key scope does not match.")
    return value["value"]


def register_onboarding_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/team/onboarding",
        tags=["teams"],
        response_model=Onboarding,
        summary="Check first-run prerequisites and get a policy-approved sample input",
    )
    async def onboarding(request: Request) -> dict[str, Any]:
        principal = require_role(request, "admin", "builder", "approver", "viewer")
        settings = await effective_team_settings(request)
        team = settings.team
        records = settings.document.get("provider_keys", {})
        providers = [
            {
                "provider": provider,
                "configured": bool(os.getenv(variable) or records.get(provider)),
                "version": records.get(provider, {}).get("version", 0),
                "can_save": bool(app.state.settings.workflow_secrets_key)
                and principal["role"] == "admin"
                and not principal.get("project"),
            }
            for provider, variable in (team.provider_credentials.items() if team else [])
        ]
        available = {row["provider"] for row in providers if row["configured"]}
        policy = team.workflows.get("ResearchWorkflow") if team else None
        blockers = []
        sample = None
        if not policy:
            blockers.append("Ask your operator to register ResearchWorkflow and its research/publish tools.")
        else:
            routes = [
                route
                for route in settings.routing.routes
                if route.model_id in policy.allowed_models
                and route.backend in policy.allowed_providers
                and (not team.allowed_models or route.model_id in team.allowed_models)
                and policy.permits_egress(route.base_url or app.state.settings.ollama_base_url)
            ]
            ready = next(
                (
                    route
                    for route in routes
                    if route.simulated or route.backend not in CLOUD_BACKENDS or route.backend in available
                ),
                None,
            )
            if ready is None:
                blockers.append("Connect a provider for an approved Research model, then refresh readiness.")
            if not {"research", "publish"} <= set(policy.allowed_tools) or not {"research", "publish"} <= set(
                team.tools
            ):
                blockers.append("Ask your operator to approve the research and publish tools for this workflow.")
            if policy.cost_limit_usd <= 0 or policy.token_limit <= 0 or team.cost_limit_usd == 0:
                blockers.append("Raise the zero budget in Team settings before starting the sample.")
            sample = {
                "template_id": "research",
                "version": TEMPLATE_VERSION,
                "workflow": "ResearchWorkflow",
                "installed": "research" in settings.document.get("templates", {}),
                "input": {
                    "topic": "How should our team evaluate AI agents?",
                    **({"model": ready.model_id} if ready else {}),
                },
                "ready": not blockers,
            }
        return {"providers": providers, "sample": sample, "blockers": blockers}

    @app.put(
        "/v1/team/providers/{provider}/key",
        tags=["teams"],
        response_model=ProviderSetup,
        summary="Save or rotate an encrypted team provider key; zero creates",
    )
    async def put_provider_key(request: Request, provider: str, body: ProviderKeyWrite) -> dict[str, Any]:
        require_settings_admin(request)
        settings = await effective_team_settings(request)
        if not settings.team or provider not in settings.team.provider_credentials:
            raise HTTPException(404, detail="Choose a provider already approved for this team.")
        value = body.value.get_secret_value()
        if (
            not 1 <= len(value) <= 16384
            or not value.isascii()
            or any(ord(char) < 33 or ord(char) > 126 for char in value)
        ):
            raise HTTPException(422, detail="Use a provider credential containing 1-16384 visible ASCII characters.")
        records = settings.document.get("provider_keys", {})
        if records.get(provider, {}).get("version", 0) != body.expected_version:
            raise HTTPException(409, detail="Provider key changed. Refresh readiness before rotating it.")
        record = {
            "version": body.expected_version + 1,
            "updated_at": time(),
            "ciphertext": cipher(request)
            .encrypt(json.dumps({"team": request.state.sandbox_id, "provider": provider, "value": value}).encode())
            .decode(),
        }
        await save_team_document(
            request,
            settings.document,
            {"provider_keys": {**records, provider: record}},
            "provider_key_saved",
            provider=provider,
            key=metadata(provider, record),
        )
        return {"provider": provider, "configured": True, "version": record["version"], "can_save": True}
