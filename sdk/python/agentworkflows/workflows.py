"""Workflow-safe helpers. Temporal owns history, scheduling, retries, and replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


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

    async def model(self, messages: list[dict[str, Any]], *, model: str, max_tokens: int = 512) -> dict[str, Any]:
        return await self._call(
            Call(
                "model",
                {"model": model, "messages": messages, "max_tokens": max_tokens},
                self.budget,
                data_classification=self.data_classification,
            )
        )

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
