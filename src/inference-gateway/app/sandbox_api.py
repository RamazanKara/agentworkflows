"""Sandbox-scoped read routes and the agent-action receipt intake.

The reads (budget, usage, models) are the data layer the admin console and client SDK
render; the receipt intake (ADR 0014) is the one governed write surface that records
claims without enforcing anything. All of them answer only for the caller's bound
sandbox, which the auth middleware pinned before any handler runs.
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import UTC, datetime
from time import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from app.audit import chain_audit_event, emit_audit_record
from app.budget import BudgetBackendError, SandboxBudgetTracker
from app.governance import effective_settings, route_permitted
from app.metrics import AGENT_RECEIPTS, REQUESTS
from app.policy import ModelRoutingPolicy, SandboxPolicySet
from app.receipts import ReceiptRequest, build_receipt_event, validate_receipt
from app.settings import AdmissionPolicyError, Settings


def register_sandbox_routes(app: FastAPI, settings: Settings) -> None:
    """Register the sandbox budget/usage/model reads and the receipt intake."""

    @app.get(
        "/v1/usage/export",
        tags=["sandbox"],
        summary="Export current UTC month team or project usage as CSV",
        description="UTF-8 CSV: team_id,project,period_start,period_end,dimension,name,calls,tokens,cost_usd,currency. "
        "Period end is exclusive. Total, provider and workflow rows overlap; costs include reservations at configured "
        "USD prices. All team roles may export; project-bound credentials see only their project. "
        "Spreadsheet formula names are apostrophe-prefixed. No historical-month or per-call export.",
        response_class=Response,
        responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
    )
    async def export_usage(request: Request) -> Response:
        from app.team_budget import team_cost_report
        from app.teams import require_role

        require_role(request, "admin", "builder", "approver", "viewer")
        report = await team_cost_report(request)
        if not report:
            raise HTTPException(404, detail="Monthly usage requires a configured team with projects or spend limits.")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "team_id",
                "project",
                "period_start",
                "period_end",
                "dimension",
                "name",
                "calls",
                "tokens",
                "cost_usd",
                "currency",
            ]
        )
        period = [
            datetime.fromtimestamp(report["window_start"] + offset, UTC).isoformat()
            for offset in (0, report["window_seconds"])
        ]
        project = report["project"]
        total = {
            "calls": sum(row.get("calls", 0) for row in report["providers"].values()),
            "tokens": sum(row.get("tokens", 0) for row in report["providers"].values()),
            "cost_usd": report["project_budgets"][project]["reserved_and_spent_usd"]
            if project
            else report["reserved_and_spent_usd"],
        }
        for dimension, rows in (
            ("total", {project or report["team_id"]: total}),
            ("provider", report["providers"]),
            ("workflow", report["workflows"]),
        ):
            for name, row in sorted(rows.items()):
                # Quoting alone does not stop spreadsheet formulas in team-controlled names.
                labels = [report["team_id"], project or "", *period, dimension, name]
                labels = [
                    "'" + value
                    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
                    else value
                    for value in labels
                ]
                writer.writerow(
                    [
                        *labels,
                        row.get("calls", 0),
                        row.get("tokens", 0),
                        f"{row.get('cost_usd', 0):.9f}",
                        "USD",
                    ]
                )
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="usage.csv"',
                "Cache-Control": "no-store",
            },
        )

    @app.get(
        "/v1/sandbox/budget",
        tags=["sandbox"],
        summary="Get sandbox budget usage",
        operation_id="getSandboxBudget",
    )
    async def sandbox_budget(request: Request) -> dict[str, Any]:
        # request.state.sandbox_id is the caller's bound sandbox when a principal is bound
        # (JWT tenant claim or an API-key record with a sandbox): the auth middleware rejects
        # a mismatched X-Sandbox-ID and rebinds a missing one upstream, so a bound caller can
        # only ever read its own budget here. In header-trusted mode (no binding) the header is
        # honored as-is - the documented insecure default.
        tracker: SandboxBudgetTracker = request.app.state.budget_tracker
        policy_set: SandboxPolicySet = request.app.state.sandbox_policy_set
        effective = effective_settings(request, policy_set, settings)
        try:
            return await asyncio.to_thread(tracker.snapshot, request.state.sandbox_id, effective)
        except BudgetBackendError as exc:
            raise HTTPException(
                status_code=503,
                detail={"message": "sandbox budget backend is unavailable", "reason": "budget_backend_unavailable"},
                headers={"Retry-After": "5"},
            ) from exc

    @app.get(
        "/v1/usage",
        tags=["sandbox"],
        summary="Get sandbox usage and estimated cost",
        operation_id="getSandboxUsage",
    )
    async def sandbox_usage(request: Request) -> dict[str, Any]:
        from app.team_budget import team_cost_report

        report = await team_cost_report(request)
        if report:
            providers = {
                name: {
                    "requests": row.get("calls", 0),
                    "total_tokens": row.get("tokens", 0),
                    "estimated_cost": row.get("cost_usd", 0),
                }
                for name, row in report["providers"].items()
            }
            return {
                "sandbox_id": request.state.sandbox_id,
                "currency": "USD",
                "providers": providers,
                "estimated_cost": sum(row["estimated_cost"] for row in providers.values()),
                "usage": {
                    "requests": sum(row["requests"] for row in providers.values()),
                    "estimated_tokens": sum(row["total_tokens"] for row in providers.values()),
                },
                "spend": report,
            }
        # Per-sandbox usage plus an estimated monetary cost (the data layer an admin/usage
        # console renders). USD_PER_1K_TOKENS of 0 leaves the cost model off (cost = 0).
        # The token counts here are settled: each request's worst-case admission estimate
        # was reconciled against the runtime's reported usage, so the cost figure tracks
        # what was consumed rather than what callers reserved.
        # Like /v1/sandbox/budget, this reflects only request.state.sandbox_id, which the auth
        # middleware forces to the caller's bound sandbox for a bound principal (JWT tenant
        # claim or an API-key record with a sandbox) - a bound caller cannot read another
        # tenant's usage via X-Sandbox-ID. Header-trusted mode (no binding) is unchanged.
        tracker: SandboxBudgetTracker = request.app.state.budget_tracker
        policy_set: SandboxPolicySet = request.app.state.sandbox_policy_set
        effective = effective_settings(request, policy_set, settings)
        try:
            snapshot = await asyncio.to_thread(tracker.snapshot, request.state.sandbox_id, effective)
        except BudgetBackendError as exc:
            raise HTTPException(
                status_code=503,
                detail={"message": "sandbox budget backend is unavailable", "reason": "budget_backend_unavailable"},
                headers={"Retry-After": "5"},
            ) from exc
        usage = snapshot.get("usage") or {}
        estimated_tokens = usage.get("estimated_tokens", 0) if isinstance(usage, dict) else 0
        estimated_cost = round((estimated_tokens / 1000.0) * settings.usd_per_1k_tokens, 6)
        providers = snapshot.get("providers") or {}
        if providers:
            estimated_cost = round(sum(provider["estimated_cost"] for provider in providers.values()), 9)
        return {
            "sandbox_id": request.state.sandbox_id,
            "usage": usage,
            "limits": snapshot.get("limits"),
            "estimated_cost": estimated_cost,
            "currency": settings.cost_currency,
            "usd_per_1k_tokens": settings.usd_per_1k_tokens,
            "providers": providers,
        }

    @app.get(
        "/v1/models",
        tags=["inference"],
        summary="List approved private models",
        operation_id="listModels",
    )
    async def models(request: Request) -> dict[str, Any]:
        REQUESTS.labels("/v1/models", settings.runtime_backend, "200").inc()
        policy: ModelRoutingPolicy = app.state.model_routing_policy
        # List what this caller may actually call: the gateway allowlist narrowed by the
        # sandbox policy. Advertising a model that then fails with model_not_allowed sends
        # SDK auto-discovery (and tools that pick the first listed model) down a dead end.
        effective = effective_settings(request, app.state.sandbox_policy_set, settings)
        tenant_policy = app.state.sandbox_policy_set.policies.get(request.state.sandbox_id)
        request.state.data_classification = tenant_policy.data_classification if tenant_policy else "internal"
        allowed = {route.model_id for route in policy.routes if route_permitted(request, effective, route)}
        data = [entry for entry in policy.openai_models() if entry["id"] in allowed]
        return {"object": "list", "data": data}

    @app.post(
        "/v1/receipts",
        tags=["sandbox"],
        summary="Record an agent action on the audit chain",
        description=(
            "Agent-action receipt intake (ADR 0014). Records what a workspace did beyond "
            "calling a model (denied egress, tool execution, file writes, credential "
            "requests) on the SAME tamper-evident hash chain as model calls, so "
            "'make audit-verify' checks them together. The action_type vocabulary is closed; "
            "an unknown value is rejected. This endpoint RECORDS claims and enforces nothing: "
            "a receipt reporting a denial is evidence that some other control denied the "
            "action, never the control itself, and submitting one permits nothing. The sandbox "
            "on the receipt is the caller's bound sandbox, so a workspace can only add to its "
            "own history. Off by default (AGENT_RECEIPTS_ENABLED)."
        ),
        operation_id="createAgentReceipt",
    )
    async def create_receipt(request: Request, payload: ReceiptRequest) -> dict[str, Any]:
        route = "/v1/receipts"
        status = "200"
        try:
            if not settings.agent_receipts_enabled:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "message": "agent-action receipts are not enabled",
                        "reason": "receipts_not_enabled",
                        "request_id": request.state.request_id,
                        "sandbox_id": request.state.sandbox_id,
                    },
                )
            # request.state.sandbox_id is already forced to the caller's bound sandbox by
            # the auth middleware. Rejecting a contradicting body value rather than quietly
            # overwriting it means a mis-configured producer fails loudly instead of filing
            # its receipts under the wrong tenant.
            if payload.sandbox_id is not None and payload.sandbox_id != request.state.sandbox_id:
                status = "403"
                AGENT_RECEIPTS.labels("rejected", "sandbox_identity_mismatch").inc()
                raise HTTPException(
                    status_code=403,
                    detail={
                        "message": "receipt sandbox_id does not match the caller's bound sandbox",
                        "reason": "sandbox_identity_mismatch",
                        "request_id": request.state.request_id,
                        "sandbox_id": request.state.sandbox_id,
                    },
                )
            validate_receipt(payload, settings)
            event = build_receipt_event(payload, settings, sandbox_id=request.state.sandbox_id)
            event["chain_id"] = getattr(request.app.state, "audit_chain_id", None)
            event["request_id"] = request.state.request_id
            event["traceparent"] = request.state.traceparent
            event["principal"] = getattr(request.state, "principal", None)
            event["ts"] = time()
            chain_audit_event(request, event)
            emit_audit_record(event)
            AGENT_RECEIPTS.labels(payload.action_type, payload.decision).inc()
            return {
                "recorded": True,
                "chain_id": event["chain_id"],
                "record_hash": event["record_hash"],
                "request_id": request.state.request_id,
                "sandbox_id": request.state.sandbox_id,
            }
        except AdmissionPolicyError as exc:
            status = "400"
            AGENT_RECEIPTS.labels("rejected", exc.reason).inc()
            raise HTTPException(
                status_code=400,
                detail={
                    "message": str(exc),
                    "reason": exc.reason,
                    "request_id": request.state.request_id,
                    "sandbox_id": request.state.sandbox_id,
                },
            ) from exc
        finally:
            REQUESTS.labels(route, "gateway", status).inc()
