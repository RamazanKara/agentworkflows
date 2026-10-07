"""Authorize existing agent-sandbox workspaces without giving them worker credentials."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.governance import effective_settings, governed, request_classification, reserve_budget
from app.guardrails import _apply_output_guardrail, _apply_prompt_secret_mode
from app.settings import AdmissionPolicyError, Settings
from app.workflow_api import ToolCall
from app.workflow_budget import reserve_run
from app.workflow_credentials import issue_step_credential, revoke_step_credential


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    exit_code: int
    output: str = ""


def register_container_routes(app: FastAPI, settings: Settings) -> None:
    @app.post(
        "/v1/agents/{agent}/start", tags=["workflows"], summary="Authorize a container step in an existing workspace"
    )
    async def start_agent(request: Request, agent: str, body: ToolCall) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": json.dumps(body.arguments)}],
            "max_tokens": 0,
        }
        request.state.action_type = "agent_start"
        request.state.tool = agent
        async with governed(request, settings, route="/v1/agents/{agent}/start", payload=payload) as call:
            call.backend = "agent-sandbox"
            policy = getattr(request.state, "workflow_policy", None)
            target = policy.agents.get(agent) if policy else None
            if target is None:
                raise AdmissionPolicyError(
                    "agent_not_allowed", "Register this agent in the workflow policy's agents map."
                )
            effective = effective_settings(request, app.state.sandbox_policy_set, settings)
            request_classification(request, payload)
            effective.validate_tool_admission(payload)
            request.state.prompt_guardrail_action = _apply_prompt_secret_mode(effective, payload, call.route)
            await reserve_budget(request, effective, payload)
            await reserve_run(request, 0, target.cost_usd)
            credential = await issue_step_credential(
                request, target.timeout_seconds, f"{target.namespace}/{target.sandbox}"
            )
            return {**target.model_dump(), **credential, "arguments": json.loads(payload["messages"][0]["content"])}

    @app.post(
        "/v1/agents/{agent}/finish", tags=["workflows"], summary="Revoke a step credential and receipt its result"
    )
    async def finish_agent(request: Request, agent: str, body: AgentResult) -> dict[str, Any]:
        payload = {"messages": [{"role": "user", "content": body.output}], "max_tokens": 0}
        request.state.action_type = "agent_exec"
        request.state.tool = agent
        async with governed(request, settings, route="/v1/agents/{agent}/finish", payload=payload) as call:
            call.backend = "agent-sandbox"
            if not getattr(request.state, "workflow_run_id", None):
                raise AdmissionPolicyError("workflow_context_required", "Finish the originating workflow step.")
            await revoke_step_credential(request, body.credential_id, completed=body.exit_code == 0)
            if body.exit_code:
                raise HTTPException(
                    502,
                    detail={
                        "reason": "agent_failed",
                        "message": "Container agent failed or timed out; inspect the workspace before retrying.",
                    },
                )
            effective = effective_settings(request, app.state.sandbox_policy_set, settings)
            guarded = {"choices": [{"message": {"content": body.output}}]}
            _apply_output_guardrail(guarded, effective, call.route, request)
            if getattr(request.state, "output_guardrail_action", None) == "blocked":
                raise AdmissionPolicyError(
                    "agent_output_blocked", "Container output was withheld by the output policy."
                )
            return {"result": guarded["choices"][0]["message"]["content"]}
