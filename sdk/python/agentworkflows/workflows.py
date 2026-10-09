"""Workflow-safe helpers. Temporal owns history, scheduling, retries, and replay."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError


def input_schema(schema: dict[str, Any]) -> Callable[[type[Any]], type[Any]]:
    """Declare a workflow's JSON input schema; publish it as policy inputSchema.

    This attaches metadata without wrapping Temporal's workflow class. The gateway
    validates input against the administrator-approved policy, not worker metadata.
    """

    def declare(cls: type[Any]) -> type[Any]:
        cls.input_schema = schema
        return cls

    return declare


@dataclass(frozen=True)
class Budget:
    token_limit: int = 10000
    cost_limit_usd: float = 5.0


@dataclass
class Call:
    kind: str
    payload: dict[str, Any]
    budget: Budget = field(default_factory=Budget)
    tool: str = ""
    data_classification: str = "internal"


class WorkflowGateway:
    """Use inside a @workflow.defn class; every call schedules a governed activity.

    Pass Temporal's own RetryPolicy and timedelta types to customize activity behavior.
    Policy and budget denials fail immediately. Credentials stay on the worker.
    """

    def __init__(
        self,
        budget: Budget | None = None,
        *,
        data_classification: str = "internal",
        start_to_close_timeout: timedelta = timedelta(minutes=3),
        schedule_to_close_timeout: timedelta = timedelta(minutes=15),
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.budget = budget or Budget()
        self.data_classification = data_classification
        self.start_to_close_timeout = start_to_close_timeout
        self.schedule_to_close_timeout = schedule_to_close_timeout
        self.retry_policy = retry_policy or RetryPolicy(
            initial_interval=timedelta(seconds=1),
            backoff_coefficient=2,
            maximum_interval=timedelta(seconds=30),
            maximum_attempts=5,
        )

    async def _call(self, call: Call) -> dict[str, Any]:
        return await workflow.execute_activity(
            "agentworkflows.call",
            call,
            result_type=dict[str, Any],
            start_to_close_timeout=self.start_to_close_timeout,
            schedule_to_close_timeout=self.schedule_to_close_timeout,
            retry_policy=self.retry_policy,
        )

    async def model(
        self, messages: list[dict[str, Any]], *, model: str | None = None, max_tokens: int = 512
    ) -> dict[str, Any]:
        return await self._call(
            Call(
                "model",
                {"messages": messages, "max_tokens": max_tokens, **({"model": model} if model else {})},
                self.budget,
                data_classification=self.data_classification,
            )
        )

    async def text(self, prompt: str, *, model: str | None = None, max_tokens: int = 512) -> str:
        """Return a text answer; omit model to use the gateway's configured default."""
        reply = await self.model([{"role": "user", "content": prompt}], model=model, max_tokens=max_tokens)
        return reply["choices"][0]["message"].get("content") or ""

    async def tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = await self._call(Call("tool", {"arguments": arguments}, self.budget, name, self.data_classification))
        return result["result"]

    async def agent(self, name: str, arguments: dict[str, Any]) -> Any:
        result = await self._call(Call("agent", arguments, self.budget, name, self.data_classification))
        return result["result"]

    async def container(self, name: str, arguments: dict[str, Any]) -> str:
        # Arbitrary code may have side effects that cannot be safely retried after a worker crash.
        result = await workflow.execute_activity(
            "agentworkflows.call",
            Call("container", {"arguments": arguments}, self.budget, name, self.data_classification),
            result_type=dict[str, Any],
            start_to_close_timeout=self.start_to_close_timeout,
            schedule_to_close_timeout=self.schedule_to_close_timeout,
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        return str(result["result"])


class ApprovalWorkflow:
    """Inherit in a Temporal workflow to expose a draft to the console and run API.

    Call approval once per run. The gateway supplies the authenticated reviewer;
    end users must not have direct Temporal access.
    """

    def __init__(self) -> None:
        self.stage = "running"
        self.draft = ""
        self.decision: bool | None = None
        self.reviewer = ""
        self.required_approvals = 1
        self.approved_by: list[str] = []
        self.expires_at: datetime | None = None
        self.approver_role = "approver"
        self.approval_policy_version: int | None = None

    async def approval(self, draft: str) -> bool:
        self.draft = draft
        self.stage = "awaiting_approval"
        timeout = 604800
        if workflow.patched("approval-notification-v1"):
            versioned = workflow.patched("approval-policy-v1")
            if versioned:
                self.stage = "configuring_approval"
            gate = await WorkflowGateway()._call(Call("approval_waiting", {"policy_version": 1} if versioned else {}))
            if versioned:
                if gate.get("policy_version") != 1:
                    raise ApplicationError("Upgrade the gateway to 0.9.0 before the worker SDK.", non_retryable=True)
                self.approval_policy_version = 1
                self.required_approvals = gate.get("required_approvals", 1)
                self.approver_role = gate.get("approver_role", "approver")
                timeout = gate.get("approval_timeout_seconds", 604800)
                self.expires_at = workflow.now() + timedelta(seconds=timeout)
                self.stage = "awaiting_approval"
                if gate.get("approval_required") is False:
                    self.decision, self.reviewer = True, "team policy"
        try:
            await workflow.wait_condition(lambda: self.decision is not None, timeout=timedelta(seconds=timeout))
        except TimeoutError:
            self.stage = "expired"
            raise ApplicationError(
                "Approval expired; start a new review.", non_retryable=True, type="ApprovalExpired"
            ) from None
        self.stage = "approved" if self.decision else "rejected"
        return bool(self.decision)

    @workflow.signal
    def approve(self, approved: bool, reviewer: str) -> None:
        # Early, duplicate, or late decisions must not approve a different draft.
        if (
            self.stage == "awaiting_approval"
            and self.decision is None
            and isinstance(approved, bool)
            and isinstance(reviewer, str)
            and reviewer.strip()
            and reviewer.strip() not in self.approved_by
            and (self.expires_at is None or workflow.now() < self.expires_at)
        ):
            self.reviewer = reviewer.strip()
            if approved:
                self.approved_by.append(self.reviewer)
            if not approved or len(self.approved_by) >= self.required_approvals:
                self.decision = approved

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {
            "stage": self.stage, "draft": self.draft, "reviewer": self.reviewer, "run_id": workflow.info().run_id,
            **({
                "approval_policy_version": self.approval_policy_version,
                "required_approvals": self.required_approvals, "approved_by": list(self.approved_by),
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "approver_role": self.approver_role,
            } if self.approval_policy_version else {}),
        }

    @workflow.update
    def review(self, approved: bool, reviewer: str) -> bool:
        if self.stage != "awaiting_approval" or self.decision is not None or reviewer in self.approved_by:
            return False
        count = len(self.approved_by)
        self.approve(approved, reviewer)
        return self.decision is not None or len(self.approved_by) > count
