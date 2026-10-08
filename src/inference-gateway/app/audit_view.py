"""Bounded team projections of the unchanged, process-wide audit log."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterator
from time import time
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from app.audit import AUDIT_GENESIS, advance_chain
from app.teams import require_role

MAX_EVENTS = 100_000
READ_BATCH = 500
DISABLED_MESSAGE = (
    "Audit view is off. Set SANDBOX_BUDGET_BACKEND=redis and SANDBOX_BUDGET_REDIS_URL, "
    "enable Redis persistence, and set AUDIT_LOG_ENABLED=true."
)
APPEND = """
local id = redis.call('XADD', KEYS[1], 'MAXLEN', '=', ARGV[2], '*', 'entry', ARGV[1])
local now = redis.call('TIME')
local cutoff = math.max(0, (tonumber(now[1]) - tonumber(ARGV[3])) * 1000)
redis.call('XTRIM', KEYS[1], 'MINID', '=', string.format('%.0f-0', cutoff))
redis.call('EXPIRE', KEYS[1], ARGV[3])
return id
"""


class AuditEntry(BaseModel):
    id: str
    chain_id: str
    sequence: int
    team_sequence: int
    record_hash: str
    view_prev_hash: str
    view_hash: str
    event: dict[str, Any]


class AuditPage(BaseModel):
    enabled: bool
    message: str | None = None
    events: list[AuditEntry] = Field(default_factory=list)
    next_cursor: str | None = None


class AuditPosition(BaseModel):
    chain_id: str
    sequence: int
    reason: str


class AuditVerification(BaseModel):
    enabled: bool
    message: str | None = None
    ok: bool | None = None
    checked: int = 0
    first_break: AuditPosition | None = None
    boundaries: list[AuditPosition] = Field(default_factory=list)


def view_key(request: Request) -> str:
    return f"{request.app.state.settings.sandbox_budget_key_prefix}:audit:{request.state.sandbox_id}"


def view_enabled(request: Request) -> bool:
    return request.app.state.budget_tracker.backend == "redis" and request.app.state.settings.audit_log_enabled


def append_audit_view(request: Request, event: dict[str, Any]) -> None:
    if not view_enabled(request) or not event.get("sandbox_id"):
        return
    state = request.app.state
    if not hasattr(state, "audit_view_heads"):
        state.audit_view_heads = {}
    team = event["sandbox_id"]
    count, previous = state.audit_view_heads.get(team, (0, AUDIT_GENESIS))
    entry = {
        "chain_id": event["chain_id"],
        "sequence": state.audit_chain_count,
        "team_sequence": count + 1,
        "record_hash": event["record_hash"],
        "event": event,
    }
    # A process chain interleaves teams. This extra chain detects missing team events
    # without retaining or disclosing another team's receipts to bridge those links.
    entry["view_prev_hash"], entry["view_hash"] = advance_chain(previous, entry)
    state.audit_view_heads[team] = (count + 1, entry["view_hash"])
    try:
        state.budget_tracker.client.eval(
            APPEND, 1, view_key(request), json.dumps(entry, sort_keys=True),
            MAX_EVENTS, state.settings.audit_view_retention_seconds,
        )
    except Exception:
        # Keep the original log available during an outage; advancing the view head
        # before the write makes a lost append visible to subsequent verification.
        logging.getLogger("uvicorn.error").exception("audit view event could not be stored")


def read_entries(request: Request, cursor: str | None = None, oldest_first: bool = False) -> Iterator[dict[str, Any]]:
    client = request.app.state.budget_tracker.client
    key = view_key(request)
    cutoff = max(0, int((time() - request.app.state.settings.audit_view_retention_seconds) * 1000))
    client.xtrim(key, minid=f"{cutoff}-0", approximate=False)
    tail = client.xrevrange(key, count=1)
    if not tail:
        return
    upper = f"({cursor}" if cursor else tail[0][0]
    # Verification needs one retained snapshot: trimming between read batches could
    # otherwise make a normal retention boundary look like a missing interior event.
    while rows := (
        client.xrange(key, max=upper)
        if oldest_first else client.xrevrange(key, max=upper, count=READ_BATCH)
    ):
        for stream_id, fields in rows:
            yield {**json.loads(fields["entry"]), "id": stream_id}
        if oldest_first:
            return
        upper = f"({rows[-1][0]}"


def actor_of(event: dict[str, Any]) -> str | None:
    principal = event.get("principal") or {}
    return event.get("actor") or principal.get("sub") or principal.get("key_id")


def in_range(event: dict[str, Any], start: float | None, end: float | None) -> bool:
    return (start is None or event["ts"] >= start) and (end is None or event["ts"] <= end)


def list_events(
    request: Request, start: float | None, end: float | None, event_type: str | None,
    actor: str | None, project: str | None, run_id: str | None, cursor: str | None, limit: int,
) -> dict[str, Any]:
    entries = []
    for entry in read_entries(request, cursor):
        event = entry["event"]
        if (
            not in_range(event, start, end)
            or (event_type is not None and event.get("event") != event_type)
            or (actor is not None and actor_of(event) != actor)
            or (project is not None and event.get("project") != project)
            or (run_id is not None and event.get("workflow_run_id") != run_id)
        ):
            continue
        if len(entries) == limit:
            return {"enabled": True, "events": entries, "next_cursor": entries[-1]["id"]}
        entries.append(entry)
    return {"enabled": True, "events": entries, "next_cursor": None}


def check_entry(entry: dict[str, Any], previous: dict[str, Any] | None) -> str | None:
    event = entry["event"]
    if entry["chain_id"] != event.get("chain_id") or entry["record_hash"] != event.get("record_hash"):
        return "event_metadata_mismatch"
    payload = {key: value for key, value in event.items() if key not in {"prev_hash", "record_hash"}}
    if advance_chain(event["prev_hash"], payload)[1] != event["record_hash"]:
        return "record_hash_mismatch"
    if entry["sequence"] == 1 and event["prev_hash"] != AUDIT_GENESIS:
        return "record_prev_hash_not_genesis"
    envelope = {key: value for key, value in entry.items() if key not in {"id", "view_prev_hash", "view_hash"}}
    if advance_chain(entry["view_prev_hash"], envelope)[1] != entry["view_hash"]:
        return "view_hash_mismatch"
    if previous:
        if entry["team_sequence"] != previous["team_sequence"] + 1 or entry["sequence"] <= previous["sequence"]:
            return "sequence_gap_or_reordered"
        if entry["view_prev_hash"] != previous["view_hash"]:
            return "broken_view_link"
        if entry["sequence"] == previous["sequence"] + 1 and event["prev_hash"] != previous["record_hash"]:
            return "broken_record_link"
    elif entry["team_sequence"] == 1 and entry["view_prev_hash"] != AUDIT_GENESIS:
        return "view_prev_hash_not_genesis"
    return None


def verify_events(request: Request, start: float | None, end: float | None) -> dict[str, Any]:
    result: dict[str, Any] = {"enabled": True, "ok": True, "checked": 0, "first_break": None, "boundaries": []}
    previous: dict[str, dict[str, Any]] = {}
    seen = set()
    for entry in read_entries(request, oldest_first=True):
        chain = entry["chain_id"]
        prior = previous.get(chain)
        previous[chain] = entry
        position = {"chain_id": chain, "sequence": entry["sequence"]}
        try:
            if not in_range(entry["event"], start, end):
                continue
            reason = check_entry(entry, prior)
        except (AttributeError, KeyError, TypeError, ValueError):
            reason = "malformed_event"
        if chain not in seen and entry["team_sequence"] > 1:
            result["boundaries"].append({**position, "reason": "time_range" if prior else "retained_range_start"})
        seen.add(chain)
        result["checked"] += 1
        if reason:
            result.update(ok=False, first_break={**position, "reason": reason})
            break
    return result


def require_audit_admin(request: Request, response: Response, start: float | None, end: float | None) -> None:
    principal = require_role(request, "admin")
    if principal.get("project"):
        raise HTTPException(403, detail={
            "reason": "team_role_required", "message": "Use a team admin credential without a project restriction.",
        })
    if start is not None and end is not None and start > end:
        raise HTTPException(422, detail={"reason": "audit_range_invalid", "message": "from must be at or before to."})
    response.headers["Cache-Control"] = "no-store"


async def read_view(function: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(function, *args)
    except (RedisError, OSError) as exc:
        raise HTTPException(503, detail={
            "reason": "audit_view_unavailable", "message": "Audit view Redis is unavailable. Retry after recovery.",
        }) from exc


Timestamp = Annotated[float | None, Query(ge=0, allow_inf_nan=False, description="Inclusive Unix timestamp in seconds")]


def register_audit_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/team/audit", tags=["teams"], summary="Read the team's retained audit events, newest first",
        response_model=AuditPage, responses={403: {"description": "Team admin role required"}},
    )
    async def audit_list(
        request: Request, response: Response,
        start: Annotated[float | None, Query(alias="from", ge=0, allow_inf_nan=False)] = None,
        to: Timestamp = None,
        event_type: Annotated[str | None, Query(max_length=128)] = None,
        actor: Annotated[str | None, Query(max_length=512)] = None,
        project: Annotated[str | None, Query(max_length=128)] = None,
        run_id: Annotated[str | None, Query(max_length=128)] = None,
        cursor: Annotated[str | None, Query(pattern=r"^[0-9]+-[0-9]+$", max_length=50)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        require_audit_admin(request, response, start, to)
        if not view_enabled(request):
            return {"enabled": False, "message": DISABLED_MESSAGE}
        return await read_view(list_events, request, start, to, event_type, actor, project, run_id, cursor, limit)

    @app.get(
        "/v1/team/audit/verify", tags=["teams"], summary="Verify original receipts and the team projection chain",
        response_model=AuditVerification, responses={403: {"description": "Team admin role required"}},
    )
    async def audit_verify(
        request: Request, response: Response,
        start: Annotated[float | None, Query(alias="from", ge=0, allow_inf_nan=False)] = None,
        to: Timestamp = None,
    ) -> dict[str, Any]:
        require_audit_admin(request, response, start, to)
        if not view_enabled(request):
            return {"enabled": False, "message": DISABLED_MESSAGE}
        return await read_view(verify_events, request, start, to)
