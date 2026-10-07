"""Short-lived, revocable credentials confined to one governed container step."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request

from app.request_context import _api_key_from_request
from app.workflow_budget import redis_call


def credential_key(request: Request, credential_id: str) -> str:
    return f"{request.app.state.settings.sandbox_budget_key_prefix}:step-credential:{credential_id}"


LEASE = "return redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2])"
RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"


async def issue_step_credential(request: Request, seconds: int, workspace: str) -> dict[str, Any]:
    token = "awf_" + secrets.token_urlsafe(32)
    credential_id = hashlib.sha256(token.encode()).hexdigest()
    lease = f"{request.app.state.settings.sandbox_budget_key_prefix}:workspace:{workspace}"
    if not await redis_call(request, "eval", LEASE, 1, lease, credential_id, seconds + 30):
        raise HTTPException(
            409,
            detail={
                "reason": "workspace_busy",
                "message": "This workspace already has a running step; wait for it to finish before starting another.",
            },
        )
    await redis_call(
        request,
        "setex",
        credential_key(request, credential_id),
        seconds,
        json.dumps(
            {
                "team": request.state.sandbox_id,
                "run": request.state.workflow_run_id,
                "step": request.state.workflow_step_id,
                "principal": request.state.principal,
                "budgets": request.state.key_budget_updates,
                "classification": request.state.data_classification,
                "lease": lease,
            }
        ),
    )
    return {"api_key": token, "credential_id": credential_id, "expires_at": time.time() + seconds}


async def bind_step_credential(request: Request) -> bool:
    token = _api_key_from_request(request, request.app.state.settings)
    if not token or not token.startswith("awf_"):
        return False
    credential_id = hashlib.sha256(token.encode()).hexdigest()
    raw = await redis_call(request, "get", credential_key(request, credential_id))
    if raw is None:
        raise HTTPException(
            401,
            detail={
                "reason": "step_credential_expired",
                "message": "This step credential expired or was revoked; start a new container activity.",
            },
        )
    identity = json.loads(raw)
    path = request.url.path
    allowed = (
        request.method == "POST"
        and (
            path in {"/v1/chat/completions", "/v1/messages"}
            or (path.startswith("/v1/tools/") and path.endswith("/call"))
        )
    ) or (request.method == "GET" and path == "/v1/tools")
    run = getattr(request.state, "workflow_run_id", identity["run"])
    step = getattr(request.state, "workflow_step_id", identity["step"])
    if (
        not allowed
        or run != identity["run"]
        or (step != identity["step"] and not step.startswith(identity["step"] + "/"))
        or request.headers.get("x-sandbox-id", identity["team"]) != identity["team"]
    ):
        raise HTTPException(
            403,
            detail={
                "reason": "step_credential_scope",
                "message": "Step credentials only permit model/tool calls for their assigned team, run, and step.",
            },
        )
    request.state.sandbox_id = identity["team"]
    request.state.sandbox_bound = True
    request.state.workflow_run_id = identity["run"]
    request.state.workflow_step_id = step
    request.state.data_classification = identity["classification"]
    request.state.key_budget_updates = identity["budgets"]
    request.state.principal = {"auth": "workflow_step", "issued_by": identity["principal"]}
    return True


async def revoke_step_credential(request: Request, credential_id: str, *, completed: bool) -> None:
    key = credential_key(request, credential_id)
    raw = await redis_call(request, "get", key)
    if raw:
        identity: dict[str, Any] = json.loads(raw)
        if (identity["team"], identity["run"], identity["step"]) != (
            request.state.sandbox_id,
            request.state.workflow_run_id,
            request.state.workflow_step_id,
        ):
            raise HTTPException(403, detail="Only the originating team and step may finish this container activity.")
        await redis_call(request, "delete", key)
        if completed:
            await redis_call(request, "eval", RELEASE, 1, identity["lease"], credential_id)
