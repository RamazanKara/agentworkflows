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
from app.storage import storage_call
from app.team_settings import effective_team_settings
from app.workflow_budget import INIT, RunBudget, effective_run_limits, nanodollars, redis_call, reserve_run, run_key
from app.workflow_content import capture_field, capture_input, capture_mode


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
    from app.workflow_operations import describe_run, register_operation_routes, save_metadata

    register_operation_routes(app)
    from app.workflow_secrets import register_secret_routes
    from app.workflow_templates import register_template_routes

    register_secret_routes(app)
    register_template_routes(app)
    from app.workflow_notifications import register_notification_routes
    from app.workflow_triggers import register_trigger_routes

    register_trigger_routes(app)
    register_notification_routes(app)

    @app.put("/v1/workflow-runs/{run_id}", tags=["workflows"], summary="Initialize an immutable per-run budget")
    async def initialize_run(request: Request, run_id: UUID, budget: RunBudget) -> dict[str, Any]:
        team = (await effective_team_settings(request)).team
        project = None
        if team and team.projects:
            from app.teams import project_access

            workflow_id = request.headers.get("x-workflow-id", "")
            parts = workflow_id.split("/")
            if len(parts) != 3 or parts[0] != request.state.sandbox_id:
                raise HTTPException(
                    403, detail="Worker workflow ID must be team/project/id; start runs through the workflow API."
                )
            project = project_access(request, parts[1])
            intent = await storage_call(request, "get_intent", workflow_id)
            if intent:
                await save_metadata(request, str(run_id), intent)
        policy = team.workflows.get(budget.workflow) if team else None
        if team and team.workflows and policy is None:
            raise HTTPException(
                403,
                detail={
                    "reason": "workflow_not_allowed",
                    "message": "Set workflow to a name from GET /v1/workflow-policies.",
                },
            )
        result = await redis_call(
            request,
            "eval",
            INIT,
            1,
            run_key(request, str(run_id)),
            budget.token_limit,
            nanodollars(budget.cost_limit_usd),
            budget.workflow,
            "1" if policy else "0",
        )
        if not result:
            raise HTTPException(
                409,
                detail={
                    "reason": "workflow_budget_conflict",
                    "message": "A run's budget cannot be changed; use its original limits.",
                },
            )
        if project:
            await redis_call(request, "hset", run_key(request, str(run_id)), "project", project)
        raw = await redis_call(request, "hgetall", run_key(request, str(run_id)))
        return {"run_id": str(run_id), **budget.model_dump(), **effective_run_limits(raw, policy)}

    @app.get("/v1/workflow-runs/{run_id}", tags=["workflows"], summary="Inspect this team's run budget")
    async def run_usage(request: Request, run_id: UUID) -> dict[str, Any]:
        team = (await effective_team_settings(request)).team
        if team and team.projects:
            from app.teams import project_access

            metadata = await storage_call(request, "get_run", request.state.sandbox_id, str(run_id))
            if metadata:
                return await describe_run(request, str(run_id))
        raw = await redis_call(request, "hgetall", run_key(request, str(run_id)))
        if not raw:
            raise HTTPException(
                404,
                detail={
                    "reason": "workflow_run_missing",
                    "message": "No run budget exists in your team. Use agentworkflows runs list to find a run; "
                    "if it has just started, check the team's worker and inspect again.",
                },
            )
        if team and team.projects:
            project_access(request, raw.get("project"))
        return {
            "run_id": str(run_id),
            "sandbox_id": request.state.sandbox_id,
            "workflow": raw.get("workflow", ""),
            **effective_run_limits(raw, team.workflows.get(raw.get("workflow", "")) if team else None),
            "tokens": int(raw["tokens"]),
            "cost_usd": int(raw["cost"]) / 1_000_000_000,
        }

    @app.get("/v1/workflow-policies", tags=["workflows"], summary="Discover this team's workflow policies")
    async def workflow_policies(request: Request) -> dict[str, Any]:
        team = (await effective_team_settings(request)).team
        return {
            "workflows": {
                name: {
                    **policy.model_dump(by_alias=True),
                    "captureContent": capture_mode(team, policy),
                }
                for name, policy in team.workflows.items()
            }
            if team
            else {}
        }

    @app.get("/v1/tools", tags=["workflows"], summary="List this team's approved workflow tools")
    async def tools(request: Request) -> dict[str, Any]:
        policy = app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
        names = set(policy.tools) if policy else set()
        if getattr(request.state, "workflow_run_id", None):
            from app.workflow_budget import load_run_policy

            try:
                await load_run_policy(request)
            except AdmissionPolicyError as exc:
                raise HTTPException(403, detail={"reason": exc.reason, "message": str(exc)}) from exc
            workflow = request.state.workflow_policy
            if workflow:
                names.intersection_update(workflow.allowed_tools)
        return {"tools": sorted(names)}

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
            workflow = getattr(request.state, "workflow_policy", None)
            if target is None or (workflow and tool not in workflow.allowed_tools):
                raise AdmissionPolicyError(
                    "tool_not_allowed",
                    "Choose an approved tool from GET /v1/tools; ask your administrator to add missing tools.",
                )
            if workflow and not workflow.permits_egress(str(target.url)):
                raise AdmissionPolicyError(
                    "workflow_egress_denied", "Approve this tool's origin in workflow allowedEgress."
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
            await capture_input(request, effective, arguments)
            identity = (
                f"{request.state.sandbox_id}:{request.state.workflow_run_id}:{request.state.workflow_step_id}:{tool}"
            )
            headers = {"Idempotency-Key": hashlib.sha256(identity.encode()).hexdigest()}
            if target.credential_env:
                credential = os.environ.get(target.credential_env)
                if not credential:
                    raise AdmissionPolicyError(
                        "tool_not_configured", "The administrator must configure this tool's server-side credential."
                    )
                headers["Authorization"] = f"Bearer {credential}"
            request.state.tool_cost = target.cost_usd
            await reserve_budget(request, effective, payload)
            await reserve_run(request, 0, target.cost_usd)
            request.state.workflow_charge = {"tokens": 0, "cost_usd": target.cost_usd}
            from app.mcp_client import call_mcp_tool, read_tool_response

            async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, follow_redirects=False) as client:
                if target.mcp_tool:
                    call.backend = "mcp"
                    result = await call_mcp_tool(client, target, arguments, headers, settings.max_request_body_bytes)
                else:
                    result = await read_tool_response(
                        client, str(target.url), arguments, headers, settings.max_request_body_bytes
                    )
            guarded = {"choices": [{"message": {"content": json.dumps(result)}}]}
            _apply_output_guardrail(guarded, effective, call.route, request)
            text = guarded["choices"][0]["message"]["content"]
            if getattr(request.state, "output_guardrail_action", None) == "blocked":
                raise AdmissionPolicyError("tool_output_blocked", "Tool output was withheld by the output policy.")
            await capture_field(request, effective, "output", json.loads(text))
            return {"result": json.loads(text)}
