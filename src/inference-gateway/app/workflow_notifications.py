"""Persist notification events and deliver them through team-owned destinations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from time import time
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import AnyHttpUrl, BaseModel, ConfigDict

from app.policy import SMTPNotification, TeamNotifications
from app.team_settings import effective_team_settings
from app.teams import require_role
from app.workflow_budget import redis_call, run_key
from app.workflow_escalation import EVENT as ESCALATION_EVENT
from app.workflow_escalation import open_gate
from app.workflow_operations import RPC_TIMEOUT, execution, operation_receipt, run_metadata

CLAIM = "return redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', 120)"
RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"


class ApprovalGateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_version: Literal[1]


class ApprovalGate(BaseModel):
    queued: bool
    approval_required: bool = True
    policy_version: Literal[1] | None = None
    required_approvals: int = 1
    approval_timeout_seconds: int = 604800
    approver_role: Literal["admin", "approver"] = "approver"


def channels(config: TeamNotifications) -> list[str]:
    return [
        name
        for name, enabled in (
            ("slack", config.slack_webhook_env),
            ("webhook", config.webhook_env),
            ("email", config.smtp),
        )
        if enabled
    ]


async def queue_event(request: Request, run_id: str, event: str) -> None:
    await redis_call(request, "hset", run_key(request, run_id) + ":notifications", event, "1")


def send_email(config: SMTPNotification, text: str, identifier: str) -> None:
    message = EmailMessage()
    message["Subject"] = "AgentWorkflows: run needs attention"
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message["Message-ID"] = f"<{identifier}@agentworkflows>"
    message.set_content(text)
    with smtplib.SMTP(config.host, config.port, timeout=10) as smtp:
        if config.start_tls:
            smtp.starttls(context=ssl.create_default_context())
        if config.username_env:
            username, password = os.environ[config.username_env], os.environ[config.password_env]
            smtp.login(username, password)
        refused = smtp.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


async def send_notification(config: TeamNotifications, channel: str, payload: dict[str, Any]) -> None:
    text = (
        f"AgentWorkflows · {payload['team_id']} / {payload['project']} · {payload['workflow']}: "
        f"{payload['event'].replace('_', ' ')}. Review in the console: {payload['console_url']}"
    )
    escalation = payload.get("escalation")
    if escalation:
        audience = (["every approver"] if escalation["approver_role"] else []) + escalation["reviewers"]
        if audience:
            text += f" Now open to: {', '.join(audience)}."
    if channel == "email":
        assert config.smtp is not None
        await asyncio.to_thread(send_email, config.smtp, text, payload["id"])
        return
    variable = config.slack_webhook_env if channel == "slack" else config.webhook_env
    url = AnyHttpUrl(os.environ[variable])
    if url.username or url.password or url.fragment:
        raise ValueError("notification URL must not contain userinfo or a fragment")
    async with (
        httpx.AsyncClient(timeout=10, follow_redirects=False) as client,
        client.stream(
            "POST",
            str(url),
            json={"text": text} if channel == "slack" else payload,
            headers={"Idempotency-Key": payload["id"]},
        ) as response,
    ):
        response.raise_for_status()


def settled(state: dict[str, Any]) -> bool:
    """Delivered, out of attempts, or backing off: nothing to send on this pass."""
    return bool(state.get("delivered")) or state.get("attempts", 0) >= 5 or state.get("next_at", 0) > time()


async def notify_run(request: Request, row: dict[str, Any]) -> None:
    from app.alert_rules import effective_alert_rules
    from app.workflow_operations import read_settings
    from app.workflow_retention import TERMINAL_STATES

    settings, rules = await effective_alert_rules(request, await read_settings(request))
    team = settings.team
    config = team.notifications if team else None
    if not config:
        return
    run_id = row["run_id"]
    events = await redis_call(request, "hgetall", run_key(request, run_id) + ":notifications")
    wanted = []
    status = row.get("progress", {}).get("stage", row["status"])
    if row["status"] in {"failed", "timed_out"}:
        status = "failed"
    if status in {"awaiting_approval", "failed"}:
        wanted.append(status)
    budget = row["budget"]
    if any(
        budget[limit] and budget[used] >= budget[limit] * rules.budget_threshold
        for used, limit in (("tokens", "token_limit"), ("cost_usd", "cost_limit_usd"))
    ):
        wanted.append("budget_threshold")
    # "0" records that a finished run had no slow step, so its receipts are not scanned again.
    if "slow_step" in rules.events and events.get("slow_step") not in {"0", "1"}:
        from app.storage import storage_call

        steps = await storage_call(request, "run_steps", request.state.sandbox_id, run_id)
        if any(
            step.get("latency_ms", 0) >= rules.slow_step_ms
            for step in steps
            if step.get("action_type") in {"model_call", "tool_exec"}
        ):
            wanted.append("slow_step")
        elif row["status"] in TERMINAL_STATES:
            await redis_call(request, "hset", run_key(request, run_id) + ":notifications", "slow_step", "0")
            events["slow_step"] = "0"
    for event in wanted:
        if events.get(event) != "1":
            await queue_event(request, run_id, event)
            events[event] = "1"
    escalation = row.get("approval_escalation") or {}
    for event, queued in events.items():
        targets = rules.channels
        if event == ESCALATION_EVENT:
            # Configuring escalation opts in to its notice; it may name its own destinations.
            targets = escalation["channels"] if escalation.get("channels") is not None else rules.channels
        elif event not in rules.events:
            continue
        if queued != "1":
            continue
        for channel in channels(config):
            if channel not in targets:
                continue
            key = run_key(request, run_id) + f":notification:{event}:{channel}"
            # Most retained runs are long settled; read before taking the delivery lock.
            if settled(json.loads(await redis_call(request, "get", key) or "{}")):
                continue
            claim = str(uuid4())
            if not await redis_call(request, "eval", CLAIM, 1, key + ":lock", claim):
                continue
            try:
                state = json.loads(await redis_call(request, "get", key) or "{}")
                if settled(state):
                    continue
                attempt = state.get("attempts", 0) + 1
                identifier = hashlib.sha256(key.encode()).hexdigest()
                payload = {
                    "id": identifier,
                    "event": event,
                    "team_id": team.sandbox_id,
                    "project": row["project"],
                    "workflow": row["workflow"],
                    "run_id": run_id,
                    "console_url": f"{str(config.console_url).rstrip('/')}#run/{run_id}",
                    **(
                        {
                            "escalation": {
                                field: escalation.get(field) for field in ("approver_role", "reviewers", "escalates_at")
                            }
                        }
                        if event == ESCALATION_EVENT
                        else {}
                    ),
                }
                state = {
                    "attempts": attempt,
                    "next_at": time() + min(3600, 30 * 2 ** (attempt - 1)),
                    "delivered": False,
                }
                await redis_call(request, "set", key, json.dumps(state))
                request.state.workflow_name = row["workflow"]
                await operation_receipt(
                    request,
                    run_id,
                    "notification",
                    channel=channel,
                    notification_event=event,
                    notification_id=identifier,
                    attempt=attempt,
                    outcome="attempted",
                )
                try:
                    async with asyncio.timeout(30):
                        await send_notification(config, channel, payload)
                    state["delivered"] = True
                    outcome = "delivered"
                except (httpx.HTTPError, ValueError, KeyError, OSError, smtplib.SMTPException):
                    # Transport exceptions may contain webhook URLs, tokens, recipient addresses or response bodies.
                    outcome = "failed" if attempt == 5 else "retrying"
                await operation_receipt(
                    request,
                    run_id,
                    "notification",
                    channel=channel,
                    notification_event=event,
                    notification_id=identifier,
                    attempt=attempt,
                    outcome=outcome,
                    status_code=200 if state["delivered"] else 502,
                )
                await redis_call(request, "set", key, json.dumps(state))
            finally:
                await redis_call(request, "eval", RELEASE, 1, key + ":lock", claim)


def register_notification_routes(app: FastAPI) -> None:
    @app.post(
        "/v1/workflow-runs/{run_id}/approval-waiting",
        tags=["workflows"],
        summary="Worker-only durable approval notification event",
        response_model=ApprovalGate,
    )
    async def waiting(request: Request, run_id: UUID, body: ApprovalGateRequest | None = None) -> dict[str, Any]:
        principal = require_role(request, "admin", "builder")
        if "workflows:execute" not in principal.get("scopes", []):
            raise HTTPException(403, detail="Approval events require the team worker credential.")
        data = await run_metadata(request, str(run_id))
        team = (await effective_team_settings(request)).team
        policy = team.workflows.get(data["workflow"]) if team else None
        approval = data.get("approval_policy") or {
            "approval_required": policy.approval_required if policy else True,
            "approval_threshold_usd": policy.approval_threshold_usd if policy else 0,
            "approver_role": policy.approver_role if policy else "approver",
            "required_approvals": 1,
            "approval_timeout_seconds": 604800,
        }
        if body is None and (approval["required_approvals"] > 1 or approval["approval_timeout_seconds"] != 604800):
            raise HTTPException(409, detail="Upgrade the worker SDK to 0.9.0 for this approval policy.")
        raw = await redis_call(request, "hgetall", run_key(request, str(run_id)))
        spent = int(raw.get("cost", 0)) / 1_000_000_000
        required = approval["approval_required"] and spent >= approval["approval_threshold_usd"]
        result = {
            "queued": required,
            "approval_required": required,
            "policy_version": 1 if body else None,
            **{field: approval[field] for field in ("required_approvals", "approval_timeout_seconds", "approver_role")},
        }
        if not required:
            if body is None:
                handle, _ = await execution(request, str(run_id))
                accepted = await handle.execute_update(
                    "review",
                    args=[True, "team policy"],
                    id=f"{run_id}:policy-approval",
                    rpc_timeout=RPC_TIMEOUT,
                )
                if not accepted:
                    raise HTTPException(409, detail="The run is not waiting at its approval gate.")
            await operation_receipt(
                request,
                str(run_id),
                "approval",
                approved=True,
                automatic=True,
                approval_threshold_usd=approval["approval_threshold_usd"],
                cost_usd=spent,
            )
            return result
        if approval.get("approval_escalation"):
            await open_gate(request, str(run_id))
        await queue_event(request, str(run_id), "awaiting_approval")
        return result
