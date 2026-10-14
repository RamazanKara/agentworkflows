"""Scheduled launches go through the same governed start API as the console."""

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

FINISHED = {"completed", "failed", "canceled", "terminated", "timed_out"}
# Waiting for a review can take days: back off to five minutes between status reads.
FIRST_POLL_SECONDS = 30
MAX_POLL_SECONDS = 300


def poll_delay(polls: int) -> int:
    """Seconds before status read ``polls + 1``: 30, 60, 120, 240, then every 300."""
    return min(MAX_POLL_SECONDS, FIRST_POLL_SECONDS * 2 ** min(polls, 4))


@workflow.defn(name="AgentWorkflowsTrigger")
class ScheduledTrigger:
    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        # A continued execution keeps the firing ID of the schedule action that started the chain.
        request = {"firing_id": workflow.info().run_id, **request}
        polls = int(request.pop("polls", 0))
        bounded = workflow.patched("trigger-poll-backoff-v1")
        while True:
            result = await workflow.execute_activity(
                "agentworkflows.trigger",
                request,
                result_type=dict[str, Any],
                start_to_close_timeout=timedelta(seconds=45),
                schedule_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_interval=timedelta(seconds=30)),
            )
            if result.get("paused") or result.get("status") in FINISHED:
                return result
            request["run_id"] = result["run_id"]
            # Keep the schedule action open so Temporal's SKIP overlap policy covers the target run.
            if not bounded:
                await workflow.sleep(timedelta(seconds=30))
                continue
            if workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new({**request, "polls": polls})
            await workflow.sleep(timedelta(seconds=poll_delay(polls)))
            polls += 1
