"""Expire terminal run records at a fixed deadline, including their start input."""

from datetime import datetime
from time import time
from typing import Any

from fastapi import Request

from app.workflow_budget import redis_call, run_key
from app.workflow_content import content_key

TERMINAL_STATES = {"completed", "failed", "canceled", "terminated", "timed_out", "continued_as_new"}


async def retain_run(request: Request, run_id: str, data: dict[str, Any], closed_at: datetime | None) -> int:
    settings = request.app.state.settings
    base = run_key(request, run_id)
    deadline = int((closed_at.timestamp() if closed_at else time()) + settings.run_record_retention_seconds)
    await redis_call(request, "setnx", base + ":retention", str(deadline))
    deadline = int(await redis_call(request, "get", base + ":retention") or deadline)
    keys = [
        base, f"{settings.sandbox_budget_key_prefix}:start:{data['workflow_id']}",
        *(base + suffix for suffix in (":metadata", ":timeline", ":capture", ":retention", ":notifications")),
    ]
    modes = await redis_call(request, "hgetall", base + ":capture")
    keys.extend(content_key(base, step_id) for step_id in modes)
    events = await redis_call(request, "hgetall", base + ":notifications")
    keys.extend(
        f"{base}:notification:{event}:{channel}"
        for event in events for channel in ("slack", "webhook", "email")
    )
    for key in keys:
        # LT preserves shorter content/credential TTLs and never extends an existing deadline.
        await redis_call(request, "expireat", key, deadline, lt=True)
    return deadline
