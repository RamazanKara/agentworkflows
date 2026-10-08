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
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import AnyHttpUrl

from app.policy import SMTPNotification, TeamNotifications
from app.team_settings import effective_team_settings
from app.teams import require_role
from app.workflow_budget import redis_call, run_key
from app.workflow_operations import RPC_TIMEOUT, execution, operation_receipt, run_metadata

CLAIM = "return redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', 120)"
RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"


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


async def notify_run(request: Request, row: dict[str, Any]) -> None:
    team = request.app.state.sandbox_policy_set.policies[request.state.sandbox_id]
    config = team.notifications
    if not config:
        return
    run_id = row["run_id"]
    status = row.get("progress", {}).get("stage", row["status"])
    if row["status"] in {"failed", "timed_out"}:
        status = "failed"
    if status in {"awaiting_approval", "failed"}:
        await queue_event(request, run_id, status)
    budget = row["budget"]
    if any(
        budget[limit] and budget[used] >= budget[limit] * config.budget_threshold
        for used, limit in (("tokens", "token_limit"), ("cost_usd", "cost_limit_usd"))
    ):
        await queue_event(request, run_id, "budget_threshold")
    events = await redis_call(request, "hgetall", run_key(request, run_id) + ":notifications")
    for event in events:
        for channel in channels(config):
            key = run_key(request, run_id) + f":notification:{event}:{channel}"
            claim = str(uuid4())
            if not await redis_call(request, "eval", CLAIM, 1, key + ":lock", claim):
                continue
            try:
                state = json.loads(await redis_call(request, "get", key) or "{}")
                if state.get("delivered") or state.get("attempts", 0) >= 5 or state.get("next_at", 0) > time():
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
    )
    async def waiting(request: Request, run_id: UUID) -> dict[str, Any]:
        principal = require_role(request, "admin", "builder")
        if "workflows:execute" not in principal.get("scopes", []):
            raise HTTPException(403, detail="Approval events require the team worker credential.")
        data = await run_metadata(request, str(run_id))
        team = (await effective_team_settings(request)).team
        policy = team.workflows.get(data["workflow"]) if team else None
        raw = await redis_call(request, "hgetall", run_key(request, str(run_id)))
        spent = int(raw.get("cost", 0)) / 1_000_000_000
        if policy and (not policy.approval_required or spent < policy.approval_threshold_usd):
            handle, _ = await execution(request, str(run_id))
            accepted = await handle.execute_update(
                "review", args=[True, "team policy"], id=f"{run_id}:policy-approval", rpc_timeout=RPC_TIMEOUT,
            )
            if not accepted:
                raise HTTPException(409, detail="The run is not waiting at its approval gate.")
            await operation_receipt(request, str(run_id), "approval", approved=True, automatic=True,
                                    approval_threshold_usd=policy.approval_threshold_usd, cost_usd=spent)
            return {"queued": False, "approval_required": False}
        await queue_event(request, str(run_id), "awaiting_approval")
        return {"queued": True}
