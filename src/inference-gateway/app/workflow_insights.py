"""Where a run's time and money went, and how each workflow behaves over recent runs."""

from __future__ import annotations

import asyncio
from statistics import median
from time import time
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel
from temporalio.service import RPCError, RPCStatusCode

from app.storage import storage_call
from app.teams import project_access, require_role

MODEL_ACTIONS = {"model_call", "inference_request"}
TOOL_ACTIONS = {"tool_exec", "tool_call"}
# Receipts for operations on a run (approvals, cancellations, notifications) are not work steps.
WORK_ACTIONS = MODEL_ACTIONS | TOOL_ACTIONS
SCAN_LIMIT = 200
CONCURRENCY = 8
OUTCOMES = ("completed", "rejected", "failed", "canceled", "awaiting_approval", "running")
FINISHED = ("completed", "rejected", "failed", "canceled")


def event_charge(event: dict[str, Any]) -> dict[str, float | int]:
    """Tokens and dollars one receipt charged, preferring the settled fallback attempts."""
    charge = event.get("workflow_charge") or {}
    attempts = event.get("routing_attempts", [])
    charges = [a.get("charged") or a.get("reserved") for a in attempts]
    charges = [c for c in charges if c]
    if charges:
        charge = {"tokens": sum(c["tokens"] for c in charges), "cost_usd": sum(c["cost_usd"] for c in charges)}
    return {"tokens": charge.get("tokens", 0), "cost_usd": charge.get("cost_usd", 0)}


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def summarize(
    created_at: float, steps: list[dict[str, Any]], *, now: float | None = None, waiting: bool = False
) -> dict[str, Any]:
    """Summarize receipts with ``action``, ``step_id``, ``timestamp``, ``duration_ms``, ``tokens``, ``cost_usd``.

    ``now`` is passed for a run that has not finished: elapsed time then runs to the present, and a run
    ``waiting`` for its first review shows an open review wait instead of a closed one.
    """
    ordered = sorted(steps, key=lambda step: step["timestamp"])
    work = [step for step in ordered if step["action"] in WORK_ACTIONS]
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for step in work:
        row = groups.setdefault(
            (step["step_id"] or "", step["action"]),
            {
                "step_id": step["step_id"] or "", "action": step["action"], "name": step_name(step),
                "calls": 0, "duration_ms": 0.0, "tokens": 0, "cost_usd": 0.0,
            },
        )
        row["calls"] += 1
        row["duration_ms"] += step["duration_ms"]
        row["tokens"] += step["tokens"]
        row["cost_usd"] += step["cost_usd"]
    by_step = list(groups.values())
    approvals = [step for step in ordered if step["action"] == "approval"]
    review = None
    review_open = False
    if approvals:
        # The draft was ready when the last work step before the first decision finished.
        before = [step for step in work if step["timestamp"] <= approvals[0]["timestamp"]]
        if before:
            review = max(0.0, approvals[0]["timestamp"] - before[-1]["timestamp"])
    elif waiting and now is not None and work:
        review, review_open = max(0.0, now - work[-1]["timestamp"]), True
    end = now if now is not None else (ordered[-1]["timestamp"] if ordered else None)
    return {
        "elapsed_seconds": max(0.0, end - created_at) if end is not None else None,
        "model_calls": sum(1 for step in work if step["action"] in MODEL_ACTIONS),
        "tool_calls": sum(1 for step in work if step["action"] in TOOL_ACTIONS),
        "model_ms": sum(step["duration_ms"] for step in work if step["action"] in MODEL_ACTIONS),
        "tool_ms": sum(step["duration_ms"] for step in work if step["action"] in TOOL_ACTIONS),
        "review_seconds": review,
        "review_open": review_open,
        "tokens": sum(step["tokens"] for step in work),
        "cost_usd": sum(step["cost_usd"] for step in work),
        "slowest_step": max(by_step, key=lambda row: row["duration_ms"], default=None),
        "costliest_step": max(by_step, key=lambda row: row["cost_usd"], default=None),
        "by_step": by_step,
    }


def step_name(step: dict[str, Any]) -> str:
    """What a person calls the step: the tool it ran, or the model it called."""
    return str(step.get("tool") or step.get("model") or "")


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    charge = event_charge(event)
    return {
        "action": event.get("action_type") or "",
        "step_id": event.get("workflow_step_id") or "",
        "tool": event.get("tool") or "",
        "model": event.get("model") or "",
        "timestamp": float(event.get("ts") or 0),
        "duration_ms": float(event.get("latency_ms") or 0),
        "tokens": charge["tokens"],
        "cost_usd": charge["cost_usd"],
    }


def outcome(row: dict[str, Any]) -> str:
    status = row.get("status")
    if status == "completed":
        return "rejected" if row.get("outcome") == "rejected" else "completed"
    if status in {"canceled", "terminated"}:
        return "canceled"
    if status in {"failed", "timed_out"}:
        return "failed"
    return "awaiting_approval" if (row.get("progress") or {}).get("stage") == "awaiting_approval" else "running"


def workflow_row(name: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {key: sum(1 for run in runs if run["outcome"] == key) for key in OUTCOMES}
    finished = [run for run in runs if run["outcome"] in FINISHED]
    elapsed = [run["summary"]["elapsed_seconds"] for run in finished if run["summary"]["elapsed_seconds"] is not None]
    reviews = [
        run["summary"]["review_seconds"] for run in runs
        if run["summary"]["review_seconds"] is not None and not run["summary"]["review_open"]
    ]
    costs = [run["summary"]["cost_usd"] for run in finished]
    steps: dict[str, dict[str, Any]] = {}
    for run in finished:
        for step in run["summary"]["by_step"]:
            row = steps.setdefault(
                step["step_id"], {"ms": [], "cost": [], "name": step.get("name", ""), "action": step["action"]}
            )
            row["ms"].append(step["duration_ms"] / step["calls"])
            row["cost"].append(step["cost_usd"])
    slowest = max(steps.items(), key=lambda item: median(item[1]["ms"]), default=None)
    costliest = max(steps.items(), key=lambda item: sum(item[1]["cost"]) / len(item[1]["cost"]), default=None)
    decided = sum(counts[key] for key in FINISHED)
    return {
        "workflow": name,
        "runs": len(runs),
        "outcomes": counts,
        "completion_rate": counts["completed"] / decided if decided else None,
        "median_seconds": median(elapsed) if elapsed else None,
        "p95_seconds": percentile(elapsed, 0.95),
        "median_review_seconds": median(reviews) if reviews else None,
        "average_cost_usd": sum(costs) / len(costs) if costs else None,
        "total_cost_usd": sum(run["summary"]["cost_usd"] for run in runs),
        "slowest_step": {
            "step_id": slowest[0], "name": slowest[1]["name"], "action": slowest[1]["action"],
            "median_ms": median(slowest[1]["ms"]),
        }
        if slowest
        else None,
        "costliest_step": {
            "step_id": costliest[0], "name": costliest[1]["name"], "action": costliest[1]["action"],
            "average_cost_usd": sum(costliest[1]["cost"]) / len(costliest[1]["cost"]),
        }
        if costliest and sum(costliest[1]["cost"]) > 0
        else None,
    }


class WorkflowInsight(BaseModel):
    workflow: str
    runs: int
    outcomes: dict[str, int]
    completion_rate: float | None
    median_seconds: float | None
    p95_seconds: float | None
    median_review_seconds: float | None
    average_cost_usd: float | None
    total_cost_usd: float
    slowest_step: dict[str, Any] | None
    costliest_step: dict[str, Any] | None


class InsightWindow(BaseModel):
    days: int
    start: float
    end: float


class WorkflowInsights(BaseModel):
    window: InsightWindow
    projects: list[str]
    scanned: int
    skipped: int
    truncated: bool
    workflows: list[WorkflowInsight]


def register_insight_routes(app: FastAPI) -> None:
    from app.workflow_operations import describe_run

    @app.get(
        "/v1/workflow-insights",
        tags=["observability"],
        response_model=WorkflowInsights,
        summary="Per-workflow outcomes, time, review wait and cost from recent retained runs",
    )
    async def insights(
        request: Request,
        days: int = Query(7, ge=1, le=30),
        project: str | None = None,
        workflow: str | None = Query(None, max_length=128),
    ) -> dict[str, Any]:
        principal = require_role(request, "admin", "builder", "approver", "viewer")
        team = request.app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
        if project or principal.get("project") or not (team and team.projects):
            projects = [project_access(request, project)]
        else:
            projects = list(team.projects)
        end = time()
        start = end - days * 86400
        run_ids: list[str] = []
        truncated = False
        for name in projects:
            offset = 0
            done = False
            while not done:
                page = await storage_call(request, "page_runs", request.state.sandbox_id, name, offset, 100)
                for run_id, created in page:
                    if created < start:
                        done = True
                        break
                    if len(run_ids) >= SCAN_LIMIT:
                        truncated = done = True
                        break
                    run_ids.append(run_id)
                done = done or len(page) < 100
                offset += 100
        limiter = asyncio.Semaphore(CONCURRENCY)

        async def load(run_id: str) -> dict[str, Any] | None:
            async with limiter:
                try:
                    row = await describe_run(request, run_id, timeline=False)
                    if workflow and row["workflow"] != workflow:
                        return None
                    events = await storage_call(request, "run_steps", request.state.sandbox_id, run_id)
                except HTTPException as exc:
                    if exc.status_code == 404:
                        return {}
                    raise
                except RPCError as exc:
                    if exc.status == RPCStatusCode.NOT_FOUND:
                        return {}
                    raise
            kind = outcome(row)
            live = kind in {"running", "awaiting_approval"}
            return {
                "workflow": row["workflow"],
                "outcome": kind,
                "summary": summarize(
                    row["created_at"], [normalize_event(event) for event in events],
                    now=end if live else None, waiting=kind == "awaiting_approval",
                ),
            }

        loaded = await asyncio.gather(*(load(run_id) for run_id in run_ids))
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in loaded:
            if row:
                grouped.setdefault(row["workflow"], []).append(row)
        return {
            "window": {"days": days, "start": start, "end": end},
            "projects": projects,
            "scanned": len(run_ids),
            "skipped": sum(1 for row in loaded if row == {}),
            "truncated": truncated,
            "workflows": sorted(
                (workflow_row(name, runs) for name, runs in grouped.items()),
                key=lambda row: (-row["runs"], row["workflow"]),
            ),
        }
