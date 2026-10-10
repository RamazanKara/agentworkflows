"""Prove an installation completes a governed run: the console wizard's path, scripted.

The check reads readiness, starts the team's sample workflow, waits for its draft, approves it as a
reviewer, and confirms completion, the approval receipt, the run summary and (for admins) audit-chain
verification. It records the time of each stage against a five-minute budget. It works against any
gateway: Compose, Helm on your cluster, or a restored backup.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any
from uuid import uuid4

from agentworkflows import GatewayClient, GatewayError

DEADLINE_SECONDS = 300.0
_FAILED = frozenset({"failed", "canceled", "terminated", "timed_out"})

__all__ = ["DEADLINE_SECONDS", "AcceptanceError", "first_approved_run"]


class AcceptanceError(RuntimeError):
    """The install did not complete a governed run; ``stage`` says where and ``stages`` what passed."""

    def __init__(self, stage: str, message: str, stages: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.stage = stage
        self.stages = stages


def first_approved_run(
    gateway: GatewayClient,
    *,
    approvers: Sequence[GatewayClient] = (),
    deadline: float = DEADLINE_SECONDS,
    allow_paid: bool = False,
    poll_seconds: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the sample through approval and return per-stage timings.

    ``gateway`` starts the run and, with admin rights, installs the sample and verifies the audit chain.
    The run is approved by ``gateway`` followed by each of ``approvers`` until its quorum is met; pass
    reviewers' own clients to check separation of duties. Raises :class:`AcceptanceError` at the first
    stage that fails, including when ``deadline`` seconds pass.
    """
    started = clock()
    stages: list[dict[str, Any]] = []
    notes: list[str] = []

    def done(stage: str, detail: str) -> None:
        row = {"stage": stage, "seconds": round(clock() - started, 1), "detail": detail}
        stages.append(row)
        if progress:
            progress(row)

    def fail(stage: str, message: str) -> AcceptanceError:
        return AcceptanceError(stage, message, stages)

    def wait(stage: str, what: str, observe: Callable[[], dict[str, Any] | None]) -> dict[str, Any]:
        while True:
            seen = observe()
            if seen is not None:
                return seen
            if clock() - started >= deadline:
                raise fail(stage, f"Timed out after {deadline:g} seconds waiting for {what}.")
            sleep(poll_seconds)

    try:
        setup = gateway.onboarding()
    except GatewayError as exc:
        raise fail("ready", f"Could not read readiness: {exc}") from exc
    sample = setup.get("sample")
    if setup.get("blockers") or not sample or not sample.get("ready"):
        reasons = " ".join(setup.get("blockers") or ["The team has no sample workflow."])
        raise fail("ready", f"Not ready for a first run. {reasons}")
    model = sample["input"].get("model")
    try:
        routes = gateway.models().get("data", [])
    except GatewayError as exc:
        raise fail("ready", f"Could not list models: {exc}") from exc
    row = next((item for item in routes if item.get("id") == model), None)
    simulated = bool(row and row.get("simulated"))
    if not simulated and not allow_paid:
        raise fail(
            "ready",
            f"The sample would call {model or 'a model'}, which is not simulated, so it can spend up to the "
            "workflow's cost limit on your provider account. Re-run with allow_paid (--allow-paid) to accept that.",
        )
    done("ready", f"Sample {sample['template_id']} {sample['version']} on {model or 'the default model'}"
         + (" (simulated)" if simulated else " (real provider)"))

    if not sample["installed"]:
        try:
            gateway.install_template(sample["template_id"], version=sample["version"])
        except GatewayError as exc:
            raise fail(
                "installed",
                "The sample template is not installed and this credential cannot install it. "
                f"Ask a team admin, or use an admin key. ({exc})",
            ) from exc
        done("installed", f"Installed {sample['template_id']} {sample['version']}")
    else:
        done("installed", "Already installed")

    try:
        run_id = gateway.start_run(sample["workflow"], sample["input"], request_id=str(uuid4()))["run_id"]
    except GatewayError as exc:
        raise fail("started", f"Could not start the sample: {exc}") from exc
    done("started", f"Run {run_id}")

    def check_failed(detail: dict[str, Any]) -> None:
        if detail["status"] in _FAILED:
            error = detail.get("error") or {}
            raise fail(
                "drafted",
                f"The run ended as {detail['status']} ({error.get('code', 'no code')}). "
                "Inspect its worker logs and step receipts.",
            )

    def draft_ready() -> dict[str, Any] | None:
        detail = gateway.run(run_id)
        check_failed(detail)
        if detail["status"] == "completed":
            raise fail("drafted", "The run finished without pausing for approval; check its approval policy.")
        return detail if (detail.get("progress") or {}).get("stage") == "awaiting_approval" else None

    waiting = wait("drafted", "the draft to await approval", draft_ready)
    needed = int((waiting.get("progress") or {}).get("required_approvals") or 1)
    done("drafted", f"Awaiting {needed} {'approval' if needed == 1 else 'approvals'}")

    reviewers = [gateway, *approvers]
    if len(reviewers) < needed:
        raise fail(
            "approved",
            f"This run needs {needed} distinct reviewers but {len(reviewers)} credential(s) were given. "
            f"Pass {needed - len(reviewers)} more reviewer key(s) (--approver-key-env).",
        )
    for reviewer in reviewers[:needed]:
        try:
            reviewer.approve_run(run_id)
        except GatewayError as exc:
            raise fail(
                "approved",
                "A credential could not approve. Reviewers need the admin or approver role. "
                f"Pass one with --approver-key-env. ({exc})",
            ) from exc
    done("approved", f"Approved by {needed} {'reviewer' if needed == 1 else 'reviewers'}")

    def finished() -> dict[str, Any] | None:
        detail = gateway.run(run_id)
        if detail["status"] in _FAILED:
            error = detail.get("error") or {}
            code = error.get("code", "no code")
            raise fail("completed", f"The run ended as {detail['status']} ({code}) after approval.")
        return detail if detail["status"] == "completed" else None

    final = wait("completed", "the run to finish after approval", finished)
    outcome = (final.get("result") or {}).get("status") if isinstance(final.get("result"), dict) else None
    if outcome != "published":
        raise fail("completed", f"The run completed with outcome {outcome or 'unknown'}, not published.")
    done("completed", "Published")

    timeline = final.get("timeline") or []
    if not any(step.get("action") == "approval" for step in timeline):
        raise fail("evidence", "The completed run has no approval receipt in its timeline.")
    if final.get("summary") is None:
        notes.append("The gateway did not return a run summary; it predates run insights.")
    done("evidence", f"{len(timeline)} receipts, including the approval")

    try:
        verification = gateway.verify_audit()
    except GatewayError as exc:
        if exc.status_code == 403:
            notes.append("Audit verification skipped: it needs a team admin credential.")
            done("audit", "Skipped (admin credential required)")
        else:
            raise fail("audit", f"Audit verification failed to run: {exc}") from exc
    else:
        if verification.get("enabled") is False:
            notes.append("The audit chain is disabled on this gateway; enable it before production use.")
            done("audit", "Disabled")
        elif verification.get("ok") is not True or verification.get("checked", 0) < 1:
            raise fail("audit", f"The retained audit chain did not verify: {verification.get('first_break')}")
        else:
            done("audit", f"Verified {verification['checked']} events")

    total = round(clock() - started, 1)
    if total > deadline:
        raise fail("completed", f"The run succeeded but took {total:g} seconds, over the {deadline:g}-second budget.")
    return {
        "ok": True, "run_id": run_id, "total_seconds": total, "deadline_seconds": deadline,
        "stages": stages, "notes": notes,
    }
