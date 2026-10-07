"""Scheduled launches go through the same governed start API as the console."""

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn(name="AgentWorkflowsTrigger")
class ScheduledTrigger:
    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        request = {**request, "firing_id": workflow.info().run_id}
        while True:
            result = await workflow.execute_activity(
                "agentworkflows.trigger",
                request,
                result_type=dict[str, Any],
                start_to_close_timeout=timedelta(seconds=45),
                schedule_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_interval=timedelta(seconds=30)),
            )
            if result.get("paused") or result.get("status") in {
                "completed",
                "failed",
                "canceled",
                "terminated",
                "timed_out",
            }:
                return result
            request["run_id"] = result["run_id"]
            # Keep the schedule action open so Temporal's SKIP overlap policy covers the target run.
            await workflow.sleep(timedelta(seconds=30))
