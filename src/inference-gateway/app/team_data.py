"""Team data portability, erasure and bounded retention of gateway records."""

import asyncio
import base64
import json
from dataclasses import asdict
from time import time
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.workflowservice.v1 import DeleteWorkflowExecutionRequest
from temporalio.service import RPCError, RPCStatusCode

from app.storage import storage_call
from app.team_settings import effective_team_settings, match_revision, require_settings_admin, save_team_document
from app.workflow_budget import redis_call
from app.workflow_operations import RPC_TIMEOUT, temporal_client

ENTER = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('INCR', KEYS[2])
return 1
"""
LEAVE = """
if redis.call('DECR', KEYS[1]) <= 0 then redis.call('DEL', KEYS[1]) end
return 1
"""
FREEZE = """
if redis.call('EXISTS', KEYS[3]) == 1 then return -1 end
if tonumber(redis.call('GET', KEYS[2]) or '0') > 0 then return 0 end
redis.call('SET', KEYS[3], '1')
if redis.call('EXISTS', KEYS[1]) == 1 then return 2 end
redis.call('SET', KEYS[1], 'erasing')
return 1
"""

EXTERNAL_DATA = [
    "External provider and tool records, delivered notifications, exported files and telemetry",
    "Operator audit logs, backups, object-store versions and Temporal archival",
    "Static team policy, bootstrap keys and identity-provider membership (remove before retiring the team)",
]


class RetentionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_seconds: int = Field(ge=60, le=31536000, strict=True)
    content_seconds: int = Field(ge=60, le=31536000, strict=True)
    audit_seconds: int = Field(ge=60, le=31536000, strict=True)


class TeamRetention(RetentionPolicy):
    revision: int


class TeamDataDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_team: str


class TeamDataStatus(BaseModel):
    team_id: str
    status: Literal["active", "erasing", "erased"]
    external_follow_up: list[str]


class TeamDataExport(BaseModel):
    version: Literal[1]
    team_id: str
    exported_at: float
    settings: dict[str, Any] | None
    runs: list[dict[str, Any]]
    start_intents: list[dict[str, Any]]
    keys: list[dict[str, Any]]
    audit: list[Any]
    step_content: dict[str, Any]
    temporal_histories: list[dict[str, Any]]
    responses: list[dict[str, Any]]
    files: list[dict[str, Any]]
    batches: list[dict[str, Any]]
    external_follow_up: list[str]


def data_key(request: Request, suffix: str = "state") -> str:
    return f"{request.app.state.settings.sandbox_budget_key_prefix}:team-data:{request.state.sandbox_id}:{suffix}"


async def enter_team_request(request: Request) -> bool:
    team = request.app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
    if request.app.state.budget_tracker.backend != "redis":
        return False
    if request.scope.get("method") == "GET" and request.url.path == "/v1/team":
        return False
    if not team or not team.projects or not request.url.path.startswith("/v1/"):
        return False
    if request.url.path.startswith("/v1/team/data"):
        return False
    if not await redis_call(request, "eval", ENTER, 2, data_key(request), data_key(request, "active")):
        raise HTTPException(409, detail="This team's data is being erased or has been erased. Contact the operator.")
    return True


async def leave_team_request(request: Request) -> None:
    from anyio import CancelScope

    with CancelScope(shield=True):
        await redis_call(request, "eval", LEAVE, 1, data_key(request, "active"))


def retention_values(settings: Any, document: dict[str, Any] | None) -> dict[str, int]:
    return {
        "run_seconds": settings.run_record_retention_seconds,
        "content_seconds": settings.content_retention_seconds,
        "audit_seconds": settings.audit_view_retention_seconds,
        **(document or {}).get("retention", {}),
    }


async def team_retention(request: Request) -> dict[str, int]:
    current = await effective_team_settings(request)
    return retention_values(request.app.state.settings, current.document)


async def temporal_runs(request: Request, records: dict[str, Any]) -> list[Any]:
    client = await temporal_client(request.app)
    prefix = request.state.sandbox_id + "/"
    # Visibility includes closed executions whose gateway metadata has already expired.
    runs = {
        (run.id, run.run_id): run
        async for run in client.list_workflows(query=f'WorkflowId STARTS_WITH "{prefix}"')
        if run.id.startswith(prefix)
    }
    retained = {(row["metadata"]["workflow_id"], row["run_id"]) for row in records["runs"]}
    known = {workflow_id for workflow_id, _ in runs.keys() | retained}
    retained.update((row["workflow_id"], None) for row in records["start_intents"] if row["workflow_id"] not in known)
    # Visibility is eventually consistent; durable start intents close the just-started execution gap.
    for workflow_id, run_id in retained:
        if (workflow_id, run_id) in runs:
            continue
        try:
            description = await client.get_workflow_handle(workflow_id, run_id=run_id).describe(rpc_timeout=RPC_TIMEOUT)
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
            continue
        actual_id = run_id or description.run_id
        runs[(workflow_id, actual_id)] = SimpleNamespace(id=workflow_id, run_id=actual_id, status=description.status)
    return list(runs.values())


async def scan_keys(request: Request, pattern: str) -> list[str]:
    cursor = 0
    keys = []
    while True:
        cursor, batch = await redis_call(request, "scan", cursor, match=pattern, count=500)
        keys.extend(batch)
        if not cursor:
            return keys


async def redis_team_keys(request: Request) -> list[str]:
    prefix = request.app.state.settings.sandbox_budget_key_prefix
    team = request.state.sandbox_id
    keys = []
    for pattern in (
        f"{prefix}:workflow:{team}:*",
        f"{prefix}:runs:{team}:*",
        f"{prefix}:start:{team}/*",
        f"{prefix}:{team}:*",
        f"{prefix}:trigger:{team}:*",
        f"{prefix}:hook:{team}:*",
        f"{prefix}:github-webhook:{team}:*",
        f"{prefix}:schedules:{team}",
    ):
        keys.extend(await scan_keys(request, pattern))
    for kind in ("session", "step-credential"):
        for key in await scan_keys(request, f"{prefix}:{kind}:*"):
            raw = await redis_call(request, "get", key)
            if raw and (json.loads(raw).get("sandbox_id") or json.loads(raw).get("team")) == team:
                keys.append(key)
    return keys


def portable_settings(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    from app.workflow_secrets import metadata

    return {
        **document,
        "workflow_secrets": {
            workflow: [metadata(name, record) for name, record in records.items()]
            for workflow, records in document.get("workflow_secrets", {}).items()
        },
    }


async def auxiliary_data(request: Request) -> dict[str, Any]:
    state, team = request.app.state, request.state.sandbox_id
    result: dict[str, Any] = {"responses": [], "files": [], "batches": []}
    if state.settings.responses_store_enabled:
        if state.response_store.backend != "redis":
            raise HTTPException(409, detail="Team export/erasure requires a shared Redis Responses store.")
        result["responses"] = await asyncio.to_thread(state.response_store.export_team, team)
    if state.settings.batch_api_enabled:
        if state.batch_store.backend != "redis":
            raise HTTPException(409, detail="Team export/erasure requires a shared Redis batch store.")
        result["batches"] = [
            asdict(row)
            for row in await asyncio.to_thread(
                state.batch_store.list_batches,
                team,
                2147483647,
            )
        ]
        files = await asyncio.to_thread(state.batch_store.list_files, team)
        result["files"] = [
            {
                **asdict(row),
                "content_base64": base64.b64encode(
                    await asyncio.to_thread(state.object_store.get, row.object_key),
                ).decode(),
            }
            for row in files
        ]
    return result


def register_team_data_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/team/retention",
        tags=["teams"],
        response_model=TeamRetention,
        summary="Inspect this team's gateway retention periods",
    )
    async def get_retention(request: Request) -> dict[str, Any]:
        require_settings_admin(request)
        current = await effective_team_settings(request)
        return {**retention_values(app.state.settings, current.document), "revision": current.document["revision"]}

    @app.put(
        "/v1/team/retention",
        tags=["teams"],
        response_model=TeamRetention,
        summary="Set retention for newly closed runs, new content and the retained audit view",
    )
    async def set_retention(
        request: Request, body: RetentionPolicy, if_match: Annotated[str, Header(alias="If-Match")]
    ) -> dict[str, Any]:
        require_settings_admin(request)
        current = await effective_team_settings(request)
        if match_revision(if_match) != current.document["revision"]:
            raise HTTPException(409, detail="Retention changed. Reload before saving.")
        document = await save_team_document(
            request,
            current.document,
            {"retention": body.model_dump()},
            "team_retention_changed",
            retention=body.model_dump(),
        )
        return {**body.model_dump(), "revision": document["revision"]}

    @app.get(
        "/v1/team/data",
        tags=["teams"],
        response_model=TeamDataStatus,
        summary="Inspect team erasure status and external follow-up",
    )
    async def data_status(request: Request) -> dict[str, Any]:
        require_settings_admin(request)
        return {
            "team_id": request.state.sandbox_id,
            "status": await redis_call(request, "get", data_key(request)) or "active",
            "external_follow_up": EXTERNAL_DATA,
        }

    @app.get(
        "/v1/team/data/export",
        tags=["teams"],
        response_model=TeamDataExport,
        summary="Export retained team data and Temporal histories as JSON",
    )
    async def export_data(request: Request, response: Response) -> dict[str, Any]:
        require_settings_admin(request)
        if await redis_call(request, "get", data_key(request)):
            raise HTTPException(409, detail="Export before starting erasure.")
        # Hold the same reader lease as normal API traffic so erasure cannot race this export.
        if not await redis_call(request, "eval", ENTER, 2, data_key(request), data_key(request, "active")):
            raise HTTPException(409, detail="Erasure is in progress.")
        try:
            records = await storage_call(request, "export_team", request.state.sandbox_id)
            records["settings"] = portable_settings(records["settings"])
            client = await temporal_client(app)
            histories = []
            for run in await temporal_runs(request, records):
                history = await client.get_workflow_handle(run.id, run_id=run.run_id).fetch_history()
                histories.append(
                    {"workflow_id": run.id, "run_id": run.run_id, "history": json.loads(history.to_json())}
                )
            contents = {}
            prefix = app.state.settings.sandbox_budget_key_prefix
            for key in await scan_keys(request, f"{prefix}:workflow:{request.state.sandbox_id}:*:content:*"):
                raw = await redis_call(request, "get", key)
                if raw:
                    contents[key.removeprefix(prefix + ":")] = json.loads(raw)
            response.headers["Content-Disposition"] = 'attachment; filename="team-data.json"'
            return {
                "version": 1,
                "team_id": request.state.sandbox_id,
                "exported_at": time(),
                **records,
                "step_content": contents,
                "temporal_histories": histories,
                **await auxiliary_data(request),
                "external_follow_up": EXTERNAL_DATA,
            }
        finally:
            await leave_team_request(request)

    @app.delete(
        "/v1/team/data",
        tags=["teams"],
        response_model=TeamDataStatus,
        summary="Erase a quiescent team; retain a tombstone blocking new work",
    )
    async def delete_data(request: Request, body: TeamDataDelete) -> dict[str, Any]:
        require_settings_admin(request)
        team = request.state.sandbox_id
        if body.confirm_team != team:
            raise HTTPException(422, detail="Type the authenticated team ID to confirm erasure.")
        if app.state.settings.response_cache_enabled:
            raise HTTPException(409, detail="Disable the response cache and let its TTL expire before team erasure.")
        frozen = await redis_call(
            request, "eval", FREEZE, 3, data_key(request), data_key(request, "active"), data_key(request, "lock")
        )
        if frozen == -1:
            raise HTTPException(409, detail="An erasure request is already running. Wait for it to finish.")
        if not frozen:
            raise HTTPException(409, detail="Team requests are still active. Stop clients and retry after they drain.")
        try:
            records = await storage_call(request, "export_team", team)
            runs = await temporal_runs(request, records)
            if any(run.status.name == "RUNNING" for run in runs):
                if frozen == 1:
                    await redis_call(request, "delete", data_key(request))
                raise HTTPException(409, detail="Cancel running workflows and pause triggers before erasing this team.")
            try:
                extra = await auxiliary_data(request)
            except HTTPException as exc:
                if exc.status_code == 409 and frozen == 1:
                    await redis_call(request, "delete", data_key(request))
                raise
            if any(row["status"] not in {"completed", "failed", "cancelled", "expired"} for row in extra["batches"]):
                if frozen == 1:
                    await redis_call(request, "delete", data_key(request))
                raise HTTPException(409, detail="Cancel active batches and wait for the processor before erasure.")
            client = await temporal_client(app)
            for run in runs:
                try:
                    await client.workflow_service.delete_workflow_execution(
                        DeleteWorkflowExecutionRequest(
                            namespace=client.namespace,
                            workflow_execution=WorkflowExecution(workflow_id=run.id, run_id=run.run_id),
                        ),
                        timeout=RPC_TIMEOUT,
                    )
                except RPCError as exc:
                    if exc.status != RPCStatusCode.NOT_FOUND:
                        raise
            policy = app.state.sandbox_policy_set.policies[team]
            from app.workflow_triggers import schedule_id

            scheduled = json.loads(
                await redis_call(request, "get", f"{app.state.settings.sandbox_budget_key_prefix}:schedules:{team}")
                or "[]"
            )
            scheduled = set(scheduled) | {
                schedule_id(team, workflow, name)
                for workflow, workflow_policy in policy.workflows.items()
                for name, trigger in workflow_policy.triggers.items()
                if trigger.kind == "cron"
            }
            for name in scheduled:
                try:
                    await client.get_schedule_handle(name).delete(rpc_timeout=RPC_TIMEOUT)
                except RPCError as exc:
                    if exc.status != RPCStatusCode.NOT_FOUND:
                        raise
            if app.state.settings.responses_store_enabled:
                await asyncio.to_thread(app.state.response_store.delete_team, team)
            if app.state.settings.batch_api_enabled:
                for key in await asyncio.to_thread(app.state.object_store.list_keys, team + "/"):
                    await asyncio.to_thread(app.state.object_store.delete, key)
                await asyncio.to_thread(app.state.batch_store.delete_team, team)
            await storage_call(request, "delete_team", team)
            for key in await redis_team_keys(request):
                await redis_call(request, "delete", key)
            await redis_call(request, "set", data_key(request), "erased")
            from app.metrics import forget_team_metrics

            forget_team_metrics(team)
            getattr(app.state, "audit_view_heads", {}).pop(team, None)
            return {"team_id": team, "status": "erased", "external_follow_up": EXTERNAL_DATA}
        finally:
            from anyio import CancelScope

            with CancelScope(shield=True):
                await redis_call(request, "delete", data_key(request, "lock"))
