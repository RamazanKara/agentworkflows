"""Governed tool execution and run budget endpoints used by Temporal activities."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.governance import effective_settings, governed, request_classification, reserve_budget
from app.guardrails import _apply_output_guardrail, _apply_prompt_secret_mode
from app.policy import DATA_CLASSIFICATIONS
from app.settings import AdmissionPolicyError, Settings
from app.workflow_budget import INIT, RunBudget, nanodollars, redis_call, reserve_run, run_key


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any]


def bind_workflow(request: Request) -> None:
    run_id = request.headers.get("x-workflow-run-id")
    step_id = request.headers.get("x-workflow-step-id")
    if run_id is None and step_id is None:
        return
    try:
        UUID(run_id or "")
    except ValueError as exc:
        raise ValueError("X-Workflow-Run-ID must be a Temporal run UUID") from exc
    if not step_id or len(step_id) > 128 or any(not (33 <= ord(c) <= 126) for c in step_id):
        raise ValueError("X-Workflow-Step-ID must be 1-128 visible ASCII characters without spaces")
    request.state.workflow_run_id = str(UUID(run_id or ""))
    request.state.workflow_step_id = step_id


def register_workflow_routes(app: FastAPI, settings: Settings) -> None:
    @app.put("/v1/workflow-runs/{run_id}", tags=["workflows"], summary="Initialize an immutable per-run budget")
    async def initialize_run(request: Request, run_id: UUID, budget: RunBudget) -> dict[str, Any]:
        result = await redis_call(
            request,
            "eval",
            INIT,
            1,
            run_key(request, str(run_id)),
            budget.token_limit,
            nanodollars(budget.cost_limit_usd),
        )
        if not result:
            raise HTTPException(
                409,
                detail={
                    "reason": "workflow_budget_conflict",
                    "message": "A run's budget cannot be changed; use its original limits.",
                },
            )
        return {"run_id": str(run_id), **budget.model_dump()}

    @app.get("/v1/workflow-runs/{run_id}", tags=["workflows"], summary="Inspect this team's run budget")
    async def run_usage(request: Request, run_id: UUID) -> dict[str, Any]:
        raw = await redis_call(request, "hgetall", run_key(request, str(run_id)))
        if not raw:
            raise HTTPException(
                404, detail={"reason": "workflow_run_missing", "message": "No budget exists for this run in your team."}
            )
        return {
            "run_id": str(run_id),
            "sandbox_id": request.state.sandbox_id,
            "token_limit": int(raw["token_limit"]),
            "cost_limit_usd": int(raw["cost_limit"]) / 1_000_000_000,
            "tokens": int(raw["tokens"]),
            "cost_usd": int(raw["cost"]) / 1_000_000_000,
        }

    @app.get("/v1/tools", tags=["workflows"], summary="List this team's approved workflow tools")
    async def tools(request: Request) -> dict[str, Any]:
        policy = app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
        return {"tools": sorted(policy.tools) if policy else []}

    @app.post(
        "/v1/tools/{tool}/call", tags=["workflows"], summary="Execute an approved tool through gateway governance"
    )
    async def call_tool(request: Request, tool: str, body: ToolCall) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": json.dumps(body.arguments)}],
            "max_tokens": 0,
        }
        request.state.action_type = "tool_exec"
        request.state.tool = tool
        async with governed(request, settings, route="/v1/tools/{tool}/call", payload=payload) as call:
            call.backend = "tool"
            if not getattr(request.state, "workflow_run_id", None):
                raise AdmissionPolicyError(
                    "workflow_context_required", "Call tools from the Temporal workflow SDK with a run and step ID."
                )
            policy = app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
            target = policy.tools.get(tool) if policy else None
            if target is None:
                raise AdmissionPolicyError(
                    "tool_not_allowed",
                    "Choose an approved tool from GET /v1/tools; ask your administrator to add missing tools.",
                )
            classification = request_classification(request, payload)
            if DATA_CLASSIFICATIONS.index(classification) > DATA_CLASSIFICATIONS.index(target.data_classification):
                raise AdmissionPolicyError(
                    "data_classification_denied", "This tool is not approved for the run's data classification."
                )
            effective = effective_settings(request, app.state.sandbox_policy_set, settings)
            effective.validate_tool_admission(payload)
            request.state.prompt_guardrail_action = _apply_prompt_secret_mode(effective, payload, call.route)
            arguments = json.loads(payload["messages"][0]["content"])
            identity = f"{request.state.sandbox_id}:{request.state.workflow_run_id}:{request.state.workflow_step_id}"
            headers = {"Idempotency-Key": hashlib.sha256(identity.encode()).hexdigest()}
            if target.credential_env:
                credential = os.environ.get(target.credential_env)
                if not credential:
                    raise AdmissionPolicyError(
                        "tool_not_configured", "The administrator must configure this tool's server-side credential."
                    )
                headers["Authorization"] = f"Bearer {credential}"
            await reserve_budget(request, effective, payload)
            await reserve_run(request, 0, target.cost_usd)
            request.state.workflow_charge = {"tokens": 0, "cost_usd": target.cost_usd}
            async with (
                httpx.AsyncClient(timeout=settings.request_timeout_seconds, follow_redirects=False) as client,
                client.stream("POST", str(target.url), json=arguments, headers=headers) as response,
            ):
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > settings.max_request_body_bytes:
                        raise ValueError("tool response exceeds the gateway body limit")
            result = json.loads(content)
            guarded = {"choices": [{"message": {"content": json.dumps(result)}}]}
            _apply_output_guardrail(guarded, effective, call.route, request)
            text = guarded["choices"][0]["message"]["content"]
            if getattr(request.state, "output_guardrail_action", None) == "blocked":
                raise AdmissionPolicyError("tool_output_blocked", "Tool output was withheld by the output policy.")
            return {"result": json.loads(text)}
