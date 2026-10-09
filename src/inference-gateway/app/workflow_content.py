"""Expiring step content, stored separately from hash-chained audit receipts."""

import hashlib
import json
import logging
from typing import Any

from fastapi import HTTPException, Request

from app.policy import SandboxPolicy, WorkflowPolicy
from app.settings import Settings
from app.team_settings import effective_team_settings
from app.workflow_budget import redis_call, run_key


def capture_mode(team: SandboxPolicy | None, policy: WorkflowPolicy | None) -> str:
    if policy and "capture_content" in policy.model_fields_set:
        return policy.capture_content
    return team.capture_content if team else "none"


def content_key(base: str, step_id: str) -> str:
    return base + ":content:" + hashlib.sha256(step_id.encode()).hexdigest()


async def capture_input(request: Request, settings: Settings, value: Any) -> None:
    if not getattr(request.state, "workflow_run_id", None):
        return
    team = (await effective_team_settings(request)).team
    mode = capture_mode(team, getattr(request.state, "workflow_policy", None))
    if mode == "none":
        return
    request.state.step_content = {
        "input": None, "output": None, "truncated": {"input": False, "output": False}, "redaction": mode
    }
    await capture_field(request, settings, "input", value)


async def capture_field(request: Request, settings: Settings, field: str, value: Any) -> None:
    content = getattr(request.state, "step_content", None)
    if content is None:
        return
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if content["redaction"] == "redacted":
        text, _ = settings._redact_secret_text(text)
        text, _ = settings.redact_output_text(text)
    encoded = text.encode("utf-8")
    content[field] = encoded[: settings.content_max_bytes].decode("utf-8", "ignore")
    content["truncated"][field] = len(encoded) > settings.content_max_bytes
    try:
        base = run_key(request)
        await redis_call(request, "hset", base + ":capture", request.state.workflow_step_id, content["redaction"])
        await redis_call(
            request, "setex", content_key(base, request.state.workflow_step_id),
            settings.content_retention_seconds, json.dumps(content),
        )
    except HTTPException:
        # A failed content write must not cause a worker to repeat an already executed tool.
        logging.getLogger("uvicorn.error").warning("Step content write failed; check workflow Redis.")


async def step_content(request: Request, run_id: str, step_id: str) -> dict[str, Any]:
    base = run_key(request, run_id)
    raw = await redis_call(request, "get", content_key(base, step_id))
    if raw:
        return {"content": json.loads(raw)}
    mode = await redis_call(request, "hget", base + ":capture", step_id)
    return {"content": None, "content_reason": "expired" if mode else "capture_off"}
