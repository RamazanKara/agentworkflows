"""Authenticated operations on Temporal executions and their existing run accounting."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
from datetime import timedelta
from time import time
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import Client, WorkflowUpdateFailedError, WorkflowUpdateRPCTimeoutOrCancelledError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from app.audit import chain_audit_event, emit_audit_record
from app.storage import storage_call
from app.team_settings import effective_team_settings
from app.teams import project_access, require_role
from app.workflow_budget import effective_run_limits, redis_call, run_key
from app.workflow_content import step_content
from app.workflow_retention import TERMINAL_STATES, retain_run

RPC_TIMEOUT = timedelta(seconds=10)

# Find the first older (score, UUID) pair atomically, even if the cursor's run expired.
RUN_PAGE = """
local low, high = 0, redis.call('ZCARD', KEYS[1])
local score = tonumber(ARGV[1])
while low < high do
    local mid = math.floor((low + high) / 2)
    local row = redis.call('ZREVRANGE', KEYS[1], mid, mid, 'WITHSCORES')
    local newer = tonumber(row[2]) > score or (tonumber(row[2]) == score and row[1] >= ARGV[2])
    if newer then low = mid + 1 else high = mid end
end
return redis.call('ZREVRANGE', KEYS[1], low, low + tonumber(ARGV[3]), 'WITHSCORES')
"""


class RunCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    team: str
    project: str
    workflow: str | None
    status: str | None
    trigger: str | None = None
    created_at: float = Field(ge=0, allow_inf_nan=False)
    run_id: UUID


class RunPage(BaseModel):
    runs: list[dict[str, Any]]
    next_offset: int | None = Field(description="Legacy offset; use next_cursor for stable paging.")
    next_cursor: str | None = Field(description="Opaque continuation, including for empty filtered pages.")


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: str = Field(default="ResearchWorkflow", min_length=1, max_length=128)
    input: Any
    project: str | None = None
    request_id: UUID = Field(default_factory=uuid4)


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool = Field(default=True, strict=True)


async def temporal_client(app: FastAPI) -> Any:
    if getattr(app.state, "temporal_client", None) is None:
        try:
            app.state.temporal_client = await asyncio.wait_for(
                Client.connect(
                    app.state.settings.temporal_address or "temporal-frontend.workflows.svc.cluster.local:7233",
                    namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
                ),
                timeout=10,
            )
        except (RuntimeError, OSError, TimeoutError) as exc:
            raise HTTPException(
                503,
                detail={
                    "reason": "temporal_unavailable",
                    "message": "Cannot connect to Temporal; check TEMPORAL_ADDRESS and retry with the same request_id.",
                },
            ) from exc
    return app.state.temporal_client


async def run_metadata(request: Request, run_id: str) -> dict[str, Any]:
    data = await storage_call(request, "get_run", request.state.sandbox_id, run_id)
    if not data:
        raise HTTPException(
            404,
            detail={
                "reason": "workflow_run_missing",
                "message": "Run not found in your team; use agentworkflows runs list.",
            },
        )
    project_access(request, data["project"])
    return data


async def save_metadata(request: Request, run_id: str, data: dict[str, Any]) -> None:
    await storage_call(request, "save_run", request.state.sandbox_id, run_id, data)


async def record_step(request: Request) -> None:
    event = getattr(request.state, "audit_event", None)
    if not event or not getattr(request.state, "workflow_run_id", None):
        return
    team = request.app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
    if not team or not team.projects:
        return
    try:
        await storage_call(request, "append_step", request.state.sandbox_id, request.state.workflow_run_id, event)
    except HTTPException:
        logging.getLogger("uvicorn.error").warning("Run timeline write failed; use the retained audit log.")


async def execution(request: Request, run_id: str) -> tuple[Any, dict[str, Any]]:
    data = await run_metadata(request, run_id)
    client = await temporal_client(request.app)
    return client.get_workflow_handle(data["workflow_id"], run_id=run_id), data


async def describe_run(request: Request, run_id: str, *, timeline: bool = True) -> dict[str, Any]:
    data = await run_metadata(request, run_id)
    result = await storage_call(request, "get_snapshot", request.state.sandbox_id, run_id)
    if result is None:
        client = await temporal_client(request.app)
        handle = client.get_workflow_handle(data["workflow_id"], run_id=run_id)
        try:
            description = await handle.describe(rpc_timeout=RPC_TIMEOUT)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND and request.app.state.storage.backend == "postgres":
                await retain_run(request, run_id, data, None)
            raise
        if description.status.name.lower() in TERMINAL_STATES:
            deadline = await retain_run(request, run_id, data, description.close_time)
            if deadline <= time():
                raise HTTPException(404, detail={"reason": "workflow_run_missing", "message": "Run retention expired."})
        result = {k: v for k, v in data.items() if k not in {"input", "fingerprint"}}
        result.update(run_id=run_id, status=description.status.name.lower())
        if result["status"] == "completed":
            value = await handle.result(rpc_timeout=RPC_TIMEOUT)
            result["result"] = value
            # A completed run can still end in a reviewer's rejection; lists show that outcome.
            if isinstance(value, dict) and isinstance(value.get("status"), str):
                result["outcome"] = value["status"]
        if result["status"] == "running":
            try:
                result["progress"] = await handle.query("status", rpc_timeout=RPC_TIMEOUT)
            except RPCError as exc:
                if exc.status not in {RPCStatusCode.INVALID_ARGUMENT, RPCStatusCode.DEADLINE_EXCEEDED}:
                    raise
                result["progress"] = {
                    "stage": "worker_unavailable", "message": "Check the team's worker and task queue.",
                }
        raw = await redis_call(request, "hgetall", run_key(request, run_id))
        team = (await effective_team_settings(request)).team
        policy = team.workflows.get(data["workflow"]) if team else None
        result["budget"] = {
            "tokens": int(raw.get("tokens", 0)),
            "cost_usd": int(raw.get("cost", 0)) / 1_000_000_000,
            **effective_run_limits(raw, policy),
        }
        if result["status"] in TERMINAL_STATES:
            await storage_call(request, "save_snapshot", request.state.sandbox_id, run_id, result)
    if not timeline:
        result.pop("result", None)
    if timeline:
        rows = await storage_call(request, "run_steps", request.state.sandbox_id, run_id)
        result["timeline"] = []
        for event in rows:
            charge = event.get("workflow_charge") or {}
            attempts = event.get("routing_attempts", [])
            charges = [a.get("charged") or a.get("reserved") for a in attempts]
            charges = [c for c in charges if c]
            if charges:
                charge = {"tokens": sum(c["tokens"] for c in charges), "cost_usd": sum(c["cost_usd"] for c in charges)}
            result["timeline"].append(
                {
                    "step_id": event.get("workflow_step_id"),
                    "action": event.get("action_type"),
                    "provider": event.get("provider"),
                    "model": event.get("model"),
                    "tool": event.get("tool"),
                    "tokens": charge.get("tokens", 0),
                    "cost_usd": charge.get("cost_usd", 0),
                    "duration_ms": event.get("latency_ms", 0),
                    "timestamp": event["ts"],
                    "status_code": event.get("status_code"),
                    "receipt_id": event["record_hash"],
                    "chain_id": event["chain_id"],
                    "attempts": event.get("routing_attempts", []),
                    "principal": event.get("principal"),
                    "receipt": event,
                    **await step_content(request, run_id, event.get("workflow_step_id") or ""),
                }
            )
    return result


async def start_run(request: Request, body: StartRun, *, trigger: dict[str, str] | None = None) -> dict[str, Any]:
    principal = require_role(request, "admin", "builder")
    project = project_access(request, body.project)
    team = (await effective_team_settings(request)).team
    if team is None or body.workflow not in team.workflows:
        raise HTTPException(
            403,
            detail={"reason": "workflow_not_allowed", "message": "Choose a workflow from GET /v1/workflow-policies."},
        )
    schema = team.workflows[body.workflow].input_schema
    errors = schema.errors(body.input) if schema else []
    if errors:
        raise HTTPException(
            422,
            detail={
                "reason": "workflow_input_invalid",
                "message": " ".join(f"{error['field']}: {error['message']}" for error in errors),
                "fields": errors,
            },
        )
    workflow_id = f"{request.state.sandbox_id}/{project}/{body.request_id}"
    origin = {"trigger": trigger} if trigger else {}
    canonical = json.dumps({"workflow": body.workflow, "input": body.input}, sort_keys=True)
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    data = {
        "workflow_id": workflow_id,
        "workflow": body.workflow,
        "project": project,
        "team_id": request.state.sandbox_id,
        "input": body.input,
        "fingerprint": fingerprint,
        "created_at": time(),
        "submitted_by": principal,
        "approval_policy": {
            field: getattr(team.workflows[body.workflow], field)
            for field in (
                "approval_required", "approval_threshold_usd", "approver_role",
                "required_approvals", "approval_timeout_seconds",
            )
        },
        **origin,
    }
    data = await storage_call(request, "save_intent", workflow_id, data)
    if data["fingerprint"] != fingerprint:
        raise HTTPException(
            409,
            detail={
                "reason": "workflow_start_conflict",
                "message": "request_id already identifies different input; use a new request_id.",
            },
        )
    client = await temporal_client(request.app)
    try:
        handle = await client.start_workflow(
            body.workflow,
            body.input,
            id=workflow_id,
            task_queue=f"{request.state.sandbox_id}-workflows",
            execution_timeout=timedelta(days=8),
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            rpc_timeout=RPC_TIMEOUT,
        )
        run_id = handle.first_execution_run_id
    except WorkflowAlreadyStartedError as exc:
        run_id = exc.run_id
    await save_metadata(request, str(run_id), data)
    return {"run_id": run_id, "workflow_id": workflow_id, "project": project, "request_id": str(body.request_id)}


async def operation_receipt(request: Request, run_id: str, action: str, **fields: Any) -> None:
    request.state.workflow_run_id = run_id
    request.state.workflow_step_id = action
    event = {
        "event": "workflow_operation",
        "action_type": action,
        "chain_id": request.app.state.audit_chain_id,
        "sandbox_id": request.state.sandbox_id,
        "principal": request.state.principal,
        "ts": time(),
        **fields,
    }
    chain_audit_event(request, event)
    emit_audit_record(event)
    await record_step(request)


def register_operation_routes(app: FastAPI) -> None:
    @app.exception_handler(RPCError)
    async def temporal_error(request: Request, exc: RPCError) -> Any:
        from fastapi.responses import JSONResponse

        from app.request_context import _error_envelope

        status = 404 if exc.status == RPCStatusCode.NOT_FOUND else 503
        return JSONResponse(
            status_code=status,
            content=_error_envelope(
                status,
                {
                    "reason": "temporal_unavailable" if status == 503 else "workflow_run_missing",
                    "message": "Cannot operate this run. Check Temporal health, retention, and the team's worker; "
                    "retry with the same request_id.",
                },
            ),
        )

    @app.post(
        "/v1/workflow-runs", tags=["workflows"], status_code=201, summary="Start an approved workflow in your project"
    )
    async def start(request: Request, body: StartRun) -> dict[str, Any]:
        return await start_run(request, body)

    @app.get(
        "/v1/workflow-runs", tags=["workflows"], summary="List your project's runs, newest first",
        response_model=RunPage,
    )
    async def runs(
        request: Request,
        project: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        cursor: str | None = Query(
            None, min_length=1, max_length=2048,
            description="next_cursor from the previous page, with unchanged filters",
        ),
        workflow: str | None = Query(None, max_length=128),
        trigger: str | None = Query(
            None, min_length=1, max_length=128,
            description="Exact trigger name; requires workflow. Only launches recorded since v0.7.0 have provenance.",
        ),
        status: Literal[
            "running", "awaiting_approval", "completed", "failed", "canceled", "terminated", "timed_out",
            "continued_as_new"
        ]
        | None = None,
    ) -> dict[str, Any]:
        selected = project_access(request, project)
        if trigger and not workflow:
            raise HTTPException(422, detail={
                "reason": "trigger_workflow_required", "message": "Select a workflow when filtering by trigger.",
            })
        scope = {
            "team": request.state.sandbox_id, "project": selected, "workflow": workflow,
            "status": status, "trigger": trigger,
        }
        position = None
        # Page the underlying index before filtering, keeping Temporal fan-out bounded.
        # Continuations advance even when this page has no matching runs.
        if cursor:
            try:
                position = RunCursor.model_validate_json(base64.b64decode(cursor, altchars=b"-_", validate=True))
                if offset or any(getattr(position, field) != value for field, value in scope.items()):
                    raise ValueError("cursor scope changed")
            except ValueError as exc:
                raise HTTPException(422, detail={
                    "reason": "run_cursor_invalid",
                    "message": "Use next_cursor with the same project and filters, without a nonzero offset.",
                }) from exc
        ids = await storage_call(request, "page_runs", request.state.sandbox_id, selected, offset, limit + 1, position)
        rows = []
        removed = 0
        for run_id, _ in ids[:limit]:
            try:
                if workflow:
                    metadata = await run_metadata(request, run_id)
                    if metadata["workflow"] != workflow or (
                        trigger and (metadata.get("trigger") or {}).get("name") != trigger
                    ):
                        continue
                row = await describe_run(request, run_id, timeline=False)
            except HTTPException as exc:
                if exc.status_code != 404 or exc.detail.get("reason") != "workflow_run_missing":
                    raise
                await storage_call(request, "remove_run_index", request.state.sandbox_id, selected, run_id)
                removed += 1
                continue
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND:
                    raise
                # Retained Redis metadata may outlive Temporal retention.
                continue
            waiting = row.get("progress", {}).get("stage") == "awaiting_approval"
            if status and ("awaiting_approval" if waiting else row["status"]) != status:
                continue
            rows.append(row)
        return {
            "runs": rows,
            "next_offset": offset + limit - removed if len(ids) > limit and cursor is None else None,
            "next_cursor": base64.urlsafe_b64encode(RunCursor(
                **scope, created_at=ids[limit - 1][1], run_id=ids[limit - 1][0],
            ).model_dump_json().encode()).decode() if len(ids) > limit else None,
        }

    @app.post(
        "/v1/workflow-runs/{run_id}/cancel", tags=["workflows"], summary="Request cancellation of this exact execution"
    )
    async def cancel(request: Request, run_id: UUID) -> dict[str, Any]:
        require_role(request, "admin", "builder")
        handle, _ = await execution(request, str(run_id))
        await handle.cancel(rpc_timeout=RPC_TIMEOUT)
        await operation_receipt(request, str(run_id), "cancel")
        return {"run_id": str(run_id), "status": "cancellation_requested"}

    @app.post(
        "/v1/workflow-runs/{run_id}/retry",
        tags=["workflows"],
        status_code=201,
        summary="Start a fresh execution of a failed or canceled run",
    )
    async def retry(request: Request, run_id: UUID) -> dict[str, Any]:
        require_role(request, "admin", "builder")
        handle, data = await execution(request, str(run_id))
        description = await handle.describe(rpc_timeout=RPC_TIMEOUT)
        if description.status.name not in {"FAILED", "CANCELED", "TIMED_OUT", "TERMINATED"}:
            raise HTTPException(
                409,
                detail={
                    "reason": "workflow_not_retryable",
                    "message": "Only failed, canceled, terminated, or timed-out runs can be retried. "
                    "Inspect the run first.",
                },
            )
        return await start_run(
            request,
            StartRun(
                workflow=data["workflow"],
                input=data["input"],
                project=data["project"],
                request_id=uuid5(run_id, "retry"),
            ),
        )

    @app.post(
        "/v1/workflow-runs/{run_id}/approve",
        tags=["workflows"],
        summary="Review the waiting draft as your verified identity",
    )
    async def approve(request: Request, run_id: UUID, body: Approval) -> dict[str, Any]:
        principal = require_role(request, "admin", "approver")
        handle, data = await execution(request, str(run_id))
        team = (await effective_team_settings(request)).team
        policy = team.workflows.get(data["workflow"]) if team else None
        approval_policy = data.get("approval_policy", {})
        if principal["role"] != "admin":
            require_role(request, approval_policy.get("approver_role", policy.approver_role if policy else "approver"))
        reviewer = principal.get("sub") or principal.get("key_id")
        update_id = hashlib.sha256(json.dumps([str(run_id), reviewer, body.approved]).encode()).hexdigest()
        try:
            try:
                accepted = await handle.get_update_handle(update_id).result(rpc_timeout=RPC_TIMEOUT)
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND:
                    raise
                description = await handle.describe(rpc_timeout=RPC_TIMEOUT)
                accepted = False
                if description.status.name == "RUNNING":
                    progress = await handle.query("status", rpc_timeout=RPC_TIMEOUT)
                    if (
                        approval_policy.get("required_approvals", 1) > 1
                        or approval_policy.get("approval_timeout_seconds", 604800) != 604800
                    ) and progress.get("approval_policy_version") != 1:
                        raise HTTPException(
                            409, detail="Upgrade the worker SDK to 0.9.0 for this approval policy."
                        ) from None
                    if progress.get("stage") == "awaiting_approval":
                        accepted = await handle.execute_update(
                            "review", args=[body.approved, reviewer], id=update_id, rpc_timeout=RPC_TIMEOUT
                        )
        except WorkflowUpdateRPCTimeoutOrCancelledError as exc:
            raise HTTPException(
                503,
                detail={
                    "reason": "approval_pending",
                    "message": "Approval delivery is pending. Check the worker and inspect the run; "
                    "retry with the same identity and decision.",
                },
            ) from exc
        except WorkflowUpdateFailedError:
            accepted = False
        if not accepted:
            raise HTTPException(
                409,
                detail={
                    "reason": "approval_not_waiting",
                    "message": "This run has no undecided draft. Inspect its current status before reviewing.",
                },
            )
        await operation_receipt(request, str(run_id), "approval", approved=body.approved, update_id=update_id)
        return {"run_id": str(run_id), "approved": body.approved, "reviewer": reviewer}

    async def refresh_metrics() -> None:
        from app.metrics import TEAM_COST_LIMIT, TEAM_SPEND, WORKFLOW_RUNS, WORKFLOW_STATUS_REFRESH
        from app.state_migrations import migrate_run_retention
        from app.team_budget import team_cost_report
        from app.workflow_notifications import notify_run
        from app.workflow_triggers import reconcile_schedules

        migrated = False
        while True:
            try:
                if not migrated:
                    migrated = await migrate_run_retention(app)
                for team in app.state.sandbox_policy_set.policies.values():
                    counts: dict[str, int] = {}
                    request = Request(
                        {
                            "type": "http",
                            "app": app,
                            "headers": [],
                            "state": {
                                "sandbox_id": team.sandbox_id,
                                "sandbox_bound": True,
                                "principal": {"role": "admin"},
                            },
                        }
                    )
                    try:
                        await reconcile_schedules(request)
                    except (HTTPException, RPCError, OSError):
                        logging.getLogger("uvicorn.error").warning(
                            "Schedule configuration unavailable; check Temporal and team cron expressions."
                        )
                    for project in team.projects:
                        offset = 0
                        while True:
                            ids = await storage_call(request, "page_runs", team.sandbox_id, project, offset, 100)
                            removed = 0
                            for run_id, _ in ids:
                                try:
                                    row = await describe_run(request, run_id, timeline=False)
                                    await notify_run(request, row)
                                except (HTTPException, RPCError, OSError):
                                    if not await storage_call(request, "get_run", team.sandbox_id, run_id):
                                        await storage_call(
                                            request, "remove_run_index", team.sandbox_id, project, run_id,
                                        )
                                        removed += 1
                                        continue
                                    logging.getLogger("uvicorn.error").warning(
                                        "Run refresh unavailable; check Temporal, Redis and notification receipts."
                                    )
                                    continue
                                status = row["status"]
                                if row.get("progress", {}).get("stage") == "awaiting_approval":
                                    status = "awaiting_approval"
                                counts[status] = counts.get(status, 0) + 1
                            if len(ids) < 100:
                                break
                            offset += 100 - removed
                    report = await team_cost_report(request)
                    if report:
                        TEAM_SPEND.labels(team.sandbox_id).set(report["reserved_and_spent_usd"])
                        TEAM_COST_LIMIT.labels(team.sandbox_id).set(report["cost_limit_usd"] or 0)
                    for status in (
                        "running",
                        "completed",
                        "failed",
                        "canceled",
                        "timed_out",
                        "terminated",
                        "awaiting_approval",
                    ):
                        WORKFLOW_RUNS.labels(team.sandbox_id, status).set(counts.get(status, 0))
                WORKFLOW_STATUS_REFRESH.set(time())
            except (HTTPException, RPCError, OSError):
                logging.getLogger("uvicorn.error").warning(
                    "Workflow metrics refresh unavailable; check Temporal and Redis."
                )
            await asyncio.sleep(30)

    async def startup() -> None:
        app.state.workflow_metrics_task = asyncio.create_task(refresh_metrics())

    async def shutdown() -> None:
        app.state.workflow_metrics_task.cancel()
        await asyncio.gather(app.state.workflow_metrics_task, return_exceptions=True)

    app.router.add_event_handler("startup", startup)
    app.router.add_event_handler("shutdown", shutdown)
