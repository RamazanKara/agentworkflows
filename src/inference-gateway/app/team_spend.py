"""Monthly team spend alerts with a durable Redis webhook outbox."""

import asyncio
import hashlib
import json
import logging
from time import time
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from app.team_budget import CHECK_ALERTS, cost_key, month_window
from app.team_settings import effective_team_settings
from app.teams import require_role
from app.workflow_budget import nanodollars, redis_call
from app.workflow_notifications import CLAIM, RELEASE, send_notification


class SpendAlert(BaseModel):
    id: str
    level: Literal["soft", "hard"]
    limit_usd: float
    reserved_and_spent_usd: float
    requested_usd: float
    created_at: float
    webhook_status: Literal["disabled", "pending", "delivered", "failed"]
    attempts: int


class TeamSpend(BaseModel):
    team_id: str
    window_start: int
    window_end: int
    soft_limit_usd: float | None
    hard_limit_usd: float | None
    reserved_and_spent_usd: float
    status: Literal["ok", "soft_limit", "hard_limit"]
    alerts: list[SpendAlert]


async def spend_status(request: Request) -> dict[str, Any]:
    team = (await effective_team_settings(request)).team
    key, start = cost_key(request)
    raw = await redis_call(request, "hgetall", key)
    used = int(raw.get("cost", 0)) / 1_000_000_000
    soft, hard = (team.soft_cost_limit_usd, team.cost_limit_usd) if team else (None, None)
    alerts = []
    for level in ("soft", "hard"):
        if f"alert.{level}" not in raw:
            continue
        delivery = json.loads(raw.get(f"delivery.{level}", "{}"))
        enabled = bool(team and team.notifications and team.notifications.webhook_env)
        alerts.append(
            {
                **json.loads(raw[f"alert.{level}"]),
                "id": hashlib.sha256(f"{key}:{level}".encode()).hexdigest(),
                "attempts": delivery.get("attempts", 0),
                "webhook_status": "delivered"
                if delivery.get("delivered")
                else "disabled"
                if not enabled
                else "failed"
                if delivery.get("attempts", 0) >= 5
                else "pending",
            }
        )
    return {
        "team_id": request.state.sandbox_id,
        "window_start": start,
        "window_end": start + month_window()[1],
        "soft_limit_usd": soft,
        "hard_limit_usd": hard,
        "reserved_and_spent_usd": used,
        "status": "hard_limit"
        if hard is not None and used >= hard
        else "soft_limit"
        if soft is not None and used >= soft
        else "ok",
        "alerts": alerts,
    }


async def notify_spend(request: Request) -> None:
    team = (await effective_team_settings(request)).team
    if not team:
        return
    key, _ = cost_key(request)
    await redis_call(
        request,
        "eval",
        CHECK_ALERTS,
        1,
        key,
        nanodollars(team.soft_cost_limit_usd) if team.soft_cost_limit_usd is not None else -1,
        nanodollars(team.cost_limit_usd) if team.cost_limit_usd is not None else -1,
        time(),
    )
    if not team.notifications or not team.notifications.webhook_env:
        return
    for alert in (await spend_status(request))["alerts"]:
        field = f"delivery.{alert['level']}"
        lock = key + ":" + field
        claim = str(uuid4())
        if not await redis_call(request, "eval", CLAIM, 1, lock, claim):
            continue
        try:
            state = json.loads(await redis_call(request, "hget", key, field) or "{}")
            if state.get("delivered") or state.get("attempts", 0) >= 5 or state.get("next_at", 0) > time():
                continue
            attempt = state.get("attempts", 0) + 1
            state = {"attempts": attempt, "next_at": time() + min(3600, 30 * 2 ** (attempt - 1)), "delivered": False}
            await redis_call(request, "hset", key, field, json.dumps(state))
            payload = {
                **alert,
                "event": f"team_spend_{alert['level']}_limit",
                "team_id": team.sandbox_id,
                "project": "all",
                "workflow": "team spend",
                "console_url": f"{str(team.notifications.console_url).rstrip('/')}#costs",
            }
            try:
                async with asyncio.timeout(30):
                    await send_notification(team.notifications, "webhook", payload)
                state["delivered"] = True
            except (httpx.HTTPError, ValueError, KeyError, OSError):
                # Webhook URLs and transport errors can contain destination credentials.
                logging.getLogger("uvicorn.error").warning("Team spend webhook failed; delivery will be retried.")
            await redis_call(request, "hset", key, field, json.dumps(state))
        finally:
            await redis_call(request, "eval", RELEASE, 1, lock, claim)


def register_spend_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/team/spend",
        tags=["teams"],
        summary="Read monthly limits, reservations and spend alerts",
        response_model=TeamSpend,
        responses={403: {"description": "An unrestricted team credential is required"}},
    )
    async def get_spend(request: Request, response: Response) -> dict[str, Any]:
        principal = require_role(request, "admin", "builder", "approver", "viewer")
        if principal.get("project"):
            raise HTTPException(403, detail="Team spend is hidden from project-restricted credentials. Use /v1/usage.")
        response.headers["Cache-Control"] = "no-store"
        return await spend_status(request)

    async def refresh() -> None:
        while True:
            if app.state.budget_tracker.backend == "redis":
                for team in app.state.sandbox_policy_set.policies:
                    request = Request(
                        {
                            "type": "http",
                            "app": app,
                            "headers": [],
                            "state": {
                                "sandbox_id": team,
                                "sandbox_bound": True,
                                "principal": {"role": "admin"},
                            },
                        }
                    )
                    try:
                        await notify_spend(request)
                    except (HTTPException, OSError):
                        logging.getLogger("uvicorn.error").warning("Team spend alerts unavailable; check storage.")
            await asyncio.sleep(30)

    async def startup() -> None:
        app.state.team_spend_task = asyncio.create_task(refresh())

    async def shutdown() -> None:
        app.state.team_spend_task.cancel()
        await asyncio.gather(app.state.team_spend_task, return_exceptions=True)

    app.router.add_event_handler("startup", startup)
    app.router.add_event_handler("shutdown", shutdown)
