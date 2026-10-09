"""Encrypted workflow secrets; only authorized running activities can resolve values."""

import json
from time import time
from typing import Annotated, Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.team_settings import effective_team_settings, require_settings_admin, save_team_document
from app.teams import require_role
from app.workflow_operations import RPC_TIMEOUT, execution, operation_receipt

SecretName = Annotated[str, Path(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]


class SecretWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: SecretStr
    expected_version: int = Field(ge=0, strict=True)


class SecretMetadata(BaseModel):
    name: str
    version: int
    updated_at: float


class ResolvedSecret(SecretMetadata):
    value: str


def cipher(request: Request) -> Fernet:
    key = request.app.state.settings.workflow_secrets_key
    if not key:
        raise HTTPException(503, detail="Configure WORKFLOW_SECRETS_KEY with a persistent Fernet key first.")
    return Fernet(key.encode())


def metadata(name: str, record: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "version": record["version"], "updated_at": record["updated_at"]}


def register_secret_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/workflows/{workflow}/secrets",
        tags=["workflows"],
        response_model=list[SecretMetadata],
        summary="List workflow secret metadata, never values (team admin)",
    )
    async def list_secrets(request: Request, workflow: str) -> list[dict[str, Any]]:
        require_settings_admin(request)
        settings = await effective_team_settings(request)
        if not settings.team or workflow not in settings.team.workflows:
            raise HTTPException(404, detail="Workflow not found in this team.")
        return [
            metadata(name, value)
            for name, value in sorted(settings.document.get("workflow_secrets", {}).get(workflow, {}).items())
        ]

    @app.put(
        "/v1/workflows/{workflow}/secrets/{name}",
        tags=["workflows"],
        response_model=SecretMetadata,
        summary="Create or rotate a secret with an expected version; zero creates",
    )
    async def write_secret(request: Request, workflow: str, name: SecretName, body: SecretWrite) -> dict[str, Any]:
        require_settings_admin(request)
        settings = await effective_team_settings(request)
        if not settings.team or workflow not in settings.team.workflows:
            raise HTTPException(404, detail="Workflow not found in this team.")
        value = body.value.get_secret_value()
        if not 1 <= len(value.encode()) <= 16384:
            raise HTTPException(422, detail="Secret value must contain 1-16384 UTF-8 bytes.")
        secrets = settings.document.get("workflow_secrets", {})
        workflow_secrets = secrets.get(workflow, {})
        previous = workflow_secrets.get(name)
        if (previous["version"] if previous else 0) != body.expected_version:
            raise HTTPException(409, detail="Secret version changed. Reload before rotating.")
        record = {
            "version": body.expected_version + 1,
            "updated_at": time(),
            "ciphertext": cipher(request)
            .encrypt(
                json.dumps(
                    {
                        "team": request.state.sandbox_id,
                        "workflow": workflow,
                        "name": name,
                        "value": value,
                    }
                ).encode()
            )
            .decode(),
        }
        await save_team_document(
            request,
            settings.document,
            {
                "workflow_secrets": {**secrets, workflow: {**workflow_secrets, name: record}},
            },
            "workflow_secret_rotated" if previous else "workflow_secret_created",
            workflow=workflow,
            secret_name=name,
            secret_version=record["version"],
        )
        return metadata(name, record)

    @app.post(
        "/v1/workflow-runs/{run_id}/secrets/{name}/resolve",
        tags=["workflows"],
        response_model=ResolvedSecret,
        summary="Resolve a secret inside a running, scoped worker activity",
    )
    async def resolve_secret(request: Request, run_id: UUID, name: SecretName) -> dict[str, Any]:
        principal = require_role(request, "admin", "builder")
        if "workflows:execute" not in principal.get("scopes", []) or (
            getattr(request.state, "workflow_run_id", None) != str(run_id)
        ):
            raise HTTPException(403, detail="Use a worker credential with this run and step context.")
        handle, run = await execution(request, str(run_id))
        if (await handle.describe(rpc_timeout=RPC_TIMEOUT)).status.name != "RUNNING":
            raise HTTPException(409, detail="Secrets are only available to running workflows.")
        settings = await effective_team_settings(request)
        if not settings.team or run["workflow"] not in settings.team.workflows:
            raise HTTPException(403, detail="Workflow policy is no longer approved.")
        record = settings.document.get("workflow_secrets", {}).get(run["workflow"], {}).get(name)
        if not record:
            raise HTTPException(404, detail="Secret not found in this workflow.")
        try:
            value = json.loads(cipher(request).decrypt(record["ciphertext"].encode()))
        except InvalidToken as exc:
            raise HTTPException(503, detail="Secret cannot be decrypted. Check the configured encryption key.") from exc
        if (value["team"], value["workflow"], value["name"]) != (request.state.sandbox_id, run["workflow"], name):
            raise HTTPException(503, detail="Secret scope does not match its encrypted record.")
        await operation_receipt(
            request,
            str(run_id),
            "workflow_secret_accessed",
            secret_name=name,
            secret_version=record["version"],
            activity_step_id=request.state.workflow_step_id,
        )
        return {**metadata(name, record), "value": value["value"]}
