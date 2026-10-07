"""Temporal schedules and signed, idempotent team webhook ingress."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import timedelta
from time import time
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
)
from temporalio.service import RPCError, RPCStatusCode

from app.policy import WorkflowTrigger
from app.teams import project_access, require_role
from app.workflow_budget import redis_call
from app.workflow_operations import RPC_TIMEOUT, StartRun, operation_receipt, start_run, temporal_client

WEBHOOK_PATH = re.compile(r"^/v1/hooks/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_-]+)$")


def trigger_key(request: Request, workflow: str, name: str) -> str:
    return (
        f"{request.app.state.settings.sandbox_budget_key_prefix}:trigger:{request.state.sandbox_id}:{workflow}:{name}"
    )


def schedule_id(team: str, workflow: str, name: str) -> str:
    return f"agentworkflows/{team}/{workflow}/{name}"


def configured_trigger(request: Request, workflow: str, name: str) -> WorkflowTrigger:
    team = request.app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
    policy = team.workflows.get(workflow) if team else None
    trigger = policy.triggers.get(name) if policy else None
    if trigger is None:
        raise HTTPException(
            404, detail={"reason": "trigger_missing", "message": "Trigger is not configured in this team."}
        )
    project_access(request, trigger.project)
    return trigger


async def is_paused(request: Request, workflow: str, name: str, trigger: WorkflowTrigger) -> bool:
    return trigger.paused or await redis_call(request, "get", trigger_key(request, workflow, name) + ":paused") == "1"


async def bind_webhook(request: Request) -> bool:
    match = WEBHOOK_PATH.fullmatch(request.url.path)
    if request.method != "POST" or not match:
        return False
    team_id, _workflow, name = match.groups()
    team = request.app.state.sandbox_policy_set.policies.get(team_id)
    secret = os.getenv(team.webhook_secret_env, "") if team else ""
    timestamp = request.headers.get("x-aw-timestamp", "")
    delivery = request.headers.get("x-aw-delivery", "")
    signature = request.headers.get("x-aw-signature", "")
    github = not signature and bool(request.headers.get("x-hub-signature-256"))
    valid = bool(secret) and len(secret) >= 32 and bool(re.fullmatch(r"[0-9]{10}", timestamp))
    valid = valid and abs(time() - int(timestamp)) <= 300 and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", delivery))
    if valid:
        signed = f"{timestamp}.{delivery}.{request.url.path}.".encode() + await request.body()
        expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        valid = hmac.compare_digest(expected.encode(), signature.encode())
    if github and len(secret) >= 32:
        delivery = request.headers.get("x-github-delivery", "")
        body = await request.body()
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        valid = bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", delivery)) and hmac.compare_digest(
            expected.encode(), request.headers["x-hub-signature-256"].encode()
        )
        # GitHub signs only the body: its unsigned delivery header cannot serve as a replay nonce.
        delivery = "github-" + hashlib.sha256(body).hexdigest()
    if not valid:
        # Do not put attacker-controlled headers or bodies into the receipt.
        request.state.sandbox_id = team_id if team else "unknown"
        await operation_receipt(
            request, "", "trigger", trigger=name, outcome="authentication_rejected", status_code=401
        )
        raise HTTPException(
            401,
            detail={
                "reason": "webhook_auth_invalid",
                "message": "Use a fresh timestamp, delivery ID and HMAC-SHA256 signature "
                "with the team's webhook secret.",
            },
        )
    explicit = request.headers.get("x-sandbox-id")
    if explicit and explicit != team_id:
        raise HTTPException(403, detail="Webhook team does not match X-Sandbox-ID.")
    request.state.sandbox_id = team_id
    request.state.sandbox_bound = True
    request.state.principal = {"auth": "webhook", "role": "builder", "key_id": f"webhook:{name}"}
    request.state.webhook_delivery = delivery
    request.state.github_webhook = github
    # Signed webhook credentials are valid only at this exact ingress route.
    request.state.workflow_run_id = None
    return True


async def reconcile_schedules(request: Request) -> None:
    team = request.app.state.sandbox_policy_set.policies[request.state.sandbox_id]
    key = f"{request.app.state.settings.sandbox_budget_key_prefix}:schedules:{team.sandbox_id}"
    configured = {
        schedule_id(team.sandbox_id, workflow, name): (workflow, name, trigger)
        for workflow, policy in team.workflows.items()
        for name, trigger in policy.triggers.items()
        if trigger.kind == "cron"
    }
    previous = json.loads(await redis_call(request, "get", key) or "[]") if configured or team.projects else []
    if not configured and not previous:
        return
    client = await temporal_client(request.app)
    for identifier in set(previous) - configured.keys():
        try:
            await client.get_schedule_handle(identifier).delete(rpc_timeout=RPC_TIMEOUT)
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
    for identifier, (workflow, name, trigger) in configured.items():
        paused = await is_paused(request, workflow, name, trigger)
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                "AgentWorkflowsTrigger",
                {"workflow": workflow, "trigger": name},
                id=f"{team.sandbox_id}/{trigger.project}/trigger-{workflow}-{name}",
                task_queue=f"{team.sandbox_id}-workflows",
                execution_timeout=timedelta(days=8),
            ),
            spec=ScheduleSpec(cron_expressions=[trigger.cron], time_zone_name="UTC"),
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(minutes=5)),
            state=ScheduleState(paused=paused, note="Managed by the team's AgentWorkflows configuration"),
        )
        fingerprint = hashlib.sha256(trigger.model_dump_json().encode()).hexdigest()
        version_key = trigger_key(request, workflow, name) + ":spec"
        try:
            await client.create_schedule(identifier, schedule, rpc_timeout=RPC_TIMEOUT)
        except ScheduleAlreadyRunningError:
            if await redis_call(request, "get", version_key) == fingerprint:
                continue
            await client.get_schedule_handle(identifier).update(
                lambda _, desired=schedule: ScheduleUpdate(schedule=desired), rpc_timeout=RPC_TIMEOUT
            )
        await redis_call(request, "set", version_key, fingerprint)
    await redis_call(request, "set", key, json.dumps(list(configured)))


class TriggerState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: bool = Field(strict=True)


class ScheduleFiring(BaseModel):
    model_config = ConfigDict(extra="forbid")
    firing_id: UUID


async def fire(request: Request, workflow: str, name: str, delivery: str, payload: Any) -> dict[str, Any]:
    trigger = configured_trigger(request, workflow, name)
    request.state.workflow_name = workflow
    try:
        if await is_paused(request, workflow, name, trigger):
            raise HTTPException(
                409,
                detail={
                    "reason": "trigger_paused",
                    "message": "Resume this trigger in the console or CLI before sending again.",
                },
            )
        result = await start_run(
            request,
            StartRun(
                workflow=workflow,
                input=payload,
                project=trigger.project,
                request_id=uuid5(NAMESPACE_URL, f"{request.state.sandbox_id}/{workflow}/{name}/{delivery}"),
            ),
        )
    except (HTTPException, RPCError) as exc:
        await operation_receipt(
            request,
            "",
            "trigger",
            trigger=name,
            kind=trigger.kind,
            outcome="rejected",
            status_code=exc.status_code if isinstance(exc, HTTPException) else 503,
        )
        raise
    await operation_receipt(
        request, result["run_id"], "trigger", trigger=name, kind=trigger.kind, outcome="started", status_code=201
    )
    return result


def register_trigger_routes(app: FastAPI) -> None:
    @app.get("/v1/workflow-triggers", tags=["workflows"], summary="List configured triggers, schedules and pause state")
    async def listing(request: Request) -> dict[str, Any]:
        require_role(request, "admin", "builder", "approver", "viewer")
        team = app.state.sandbox_policy_set.policies[request.state.sandbox_id]
        rows = []
        for workflow, policy in team.workflows.items():
            for name, trigger in policy.triggers.items():
                if request.state.principal.get("project") not in {None, trigger.project}:
                    continue
                row = {
                    "workflow": workflow,
                    "name": name,
                    "kind": trigger.kind,
                    "project": trigger.project,
                    "cron": trigger.cron,
                    "paused": await is_paused(request, workflow, name, trigger),
                    "configuration_paused": trigger.paused,
                }
                if trigger.kind == "cron":
                    try:
                        client = await temporal_client(app)
                        desc = await client.get_schedule_handle(schedule_id(team.sandbox_id, workflow, name)).describe(
                            rpc_timeout=RPC_TIMEOUT
                        )
                        row.update(
                            paused=desc.schedule.state.paused or row["paused"],
                            next_fire_at=[value.isoformat() for value in desc.info.next_action_times[:3]],
                        )
                    except (RPCError, HTTPException):
                        row["error"] = "Schedule unavailable; check Temporal and the gateway configuration."
                else:
                    row["url"] = f"/v1/hooks/{team.sandbox_id}/{workflow}/{name}"
                    row["secret_configured"] = len(os.getenv(team.webhook_secret_env, "")) >= 32
                rows.append(row)
        return {"triggers": rows}

    @app.patch(
        "/v1/workflow-triggers/{workflow}/{name}", tags=["workflows"], summary="Pause or resume a configured trigger"
    )
    async def pause(request: Request, workflow: str, name: str, body: TriggerState) -> dict[str, Any]:
        require_role(request, "admin", "builder")
        trigger = configured_trigger(request, workflow, name)
        if trigger.paused and not body.paused:
            raise HTTPException(
                409, detail="This trigger is paused in team configuration. Ask your admin to change it first."
            )
        key = trigger_key(request, workflow, name) + ":paused"
        if body.paused:
            await redis_call(request, "set", key, "1")
        if trigger.kind == "cron":
            client = await temporal_client(app)
            handle = client.get_schedule_handle(schedule_id(request.state.sandbox_id, workflow, name))
            await (handle.pause(rpc_timeout=RPC_TIMEOUT) if body.paused else handle.unpause(rpc_timeout=RPC_TIMEOUT))
        if not body.paused:
            await redis_call(request, "set", key, "0")
        await operation_receipt(request, "", "trigger_pause", trigger=name, workflow=workflow, paused=body.paused)
        return {"workflow": workflow, "name": name, "paused": body.paused}

    @app.post(
        "/v1/workflow-triggers/{workflow}/{name}/fire",
        tags=["workflows"],
        summary="Worker-only Temporal schedule delivery",
    )
    async def scheduled(request: Request, workflow: str, name: str, body: ScheduleFiring) -> dict[str, Any]:
        principal = require_role(request, "admin", "builder")
        if "workflows:execute" not in principal.get("scopes", []):
            raise HTTPException(403, detail="Schedule delivery requires the team worker credential.")
        trigger = configured_trigger(request, workflow, name)
        if trigger.kind != "cron":
            raise HTTPException(404, detail="Not a cron trigger.")
        return await fire(request, workflow, name, str(body.firing_id), trigger.input)

    @app.post(
        "/v1/hooks/{team}/{workflow}/{name}",
        tags=["workflows"],
        status_code=201,
        summary="Start a workflow from a signed JSON webhook",
    )
    async def webhook(request: Request, team: str, workflow: str, name: str) -> dict[str, Any]:
        if request.state.principal.get("auth") != "webhook":
            raise HTTPException(401, detail="A signed webhook request is required.")
        trigger = configured_trigger(request, workflow, name)
        if trigger.kind != "webhook":
            raise HTTPException(404, detail="Not a webhook trigger.")
        delivery = request.state.webhook_delivery
        key = trigger_key(request, workflow, name) + ":delivery:" + delivery
        if request.state.github_webhook:
            key = f"{app.state.settings.sandbox_budget_key_prefix}:github-webhook:{team}:{delivery}"
            await redis_call(request, "setnx", key + ":route", request.url.path)
            if await redis_call(request, "get", key + ":route") != request.url.path:
                await operation_receipt(
                    request, "", "trigger", trigger=name, outcome="replay_rejected", status_code=409
                )
                raise HTTPException(
                    409,
                    detail={
                        "reason": "webhook_replayed",
                        "message": "This signed payload belongs to another trigger delivery.",
                    },
                )
        if await redis_call(request, "get", key):
            await operation_receipt(request, "", "trigger", trigger=name, outcome="replay_rejected", status_code=409)
            raise HTTPException(
                409,
                detail={
                    "reason": "webhook_replayed",
                    "message": "This delivery already started a run. Inspect the team's run history.",
                },
            )
        try:
            payload = json.loads(await request.body())
            json.dumps(payload, allow_nan=False)
        except (ValueError, UnicodeError):
            await operation_receipt(request, "", "trigger", trigger=name, outcome="invalid_json", status_code=422)
            raise HTTPException(422, detail="Webhook body must be valid JSON.") from None
        result = await fire(request, workflow, name, delivery, payload)
        # Deterministic workflow IDs protect concurrent deliveries and ambiguous start failures.
        await redis_call(request, "set", key, result["run_id"])
        return result
