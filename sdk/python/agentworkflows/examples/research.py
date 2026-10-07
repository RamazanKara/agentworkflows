"""Research -> draft -> human approval -> publish. Run with python -m agentworkflows.examples.research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ApplicationError

from agentworkflows.workflows import Budget, WorkflowGateway


@dataclass
class ResearchRequest:
    topic: str
    model: str = "demo-openai"
    token_limit: int = 10000
    cost_limit_usd: float = 5.0


@workflow.defn
class ResearchWorkflow:
    def __init__(self) -> None:
        self.stage = "research"
        self.draft = ""
        self.decision: bool | None = None
        self.reviewer = ""

    @workflow.run
    async def run(self, request: ResearchRequest) -> dict[str, Any]:
        gateway = WorkflowGateway(Budget(request.token_limit, request.cost_limit_usd))
        sources = await gateway.tool("research", {"query": request.topic})
        research = await gateway.model(
            [
                {
                    "role": "user",
                    "content": f"Summarize these sources on {request.topic}; "
                    f"cite URLs and flag uncertainty.\n{sources}",
                }
            ],
            model=request.model,
        )
        self.stage = "draft"
        draft = await gateway.model(
            [
                {
                    "role": "user",
                    "content": "Write a concise team briefing with citations from this research:\n"
                    + research["choices"][0]["message"]["content"],
                }
            ],
            model=request.model,
        )
        self.draft = draft["choices"][0]["message"]["content"]
        self.stage = "awaiting_approval"
        try:
            await workflow.wait_condition(lambda: self.decision is not None, timeout=timedelta(days=7))
        except TimeoutError:
            raise ApplicationError(
                "Approval expired after seven days; start a new review.", non_retryable=True
            ) from None
        if not self.decision:
            self.stage = "rejected"
            return {"status": self.stage, "reviewer": self.reviewer}
        self.stage = "publish"
        published = await gateway.tool(
            "publish", {"title": request.topic, "body": self.draft, "reviewer": self.reviewer}
        )
        self.stage = "published"
        return {"status": self.stage, "publication": published, "run_id": workflow.info().run_id}

    @workflow.signal
    def approve(self, approved: bool, reviewer: str) -> None:
        # Early, duplicate, or late decisions must not approve a different draft.
        if (
            self.stage == "awaiting_approval"
            and self.decision is None
            and isinstance(approved, bool)
            and reviewer.strip()
        ):
            self.decision = approved
            self.reviewer = reviewer.strip()

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"stage": self.stage, "draft": self.draft, "reviewer": self.reviewer, "run_id": workflow.info().run_id}


async def main() -> None:
    import argparse
    import asyncio
    import json
    import os
    from uuid import uuid4

    from temporalio.client import Client
    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.worker import Worker

    from agentworkflows.activities import GatewayActivities

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("worker", help="Poll the research task queue using a team-bound gateway key.")
    start = commands.add_parser("start", help="Start a budgeted research run and print its IDs.")
    start.add_argument("topic")
    start.add_argument("--model", default="demo-openai", help="Approved gateway model ID.")
    for command in ("status", "result", "approve", "reject"):
        sub = commands.add_parser(command)
        sub.add_argument("workflow_id")
        sub.add_argument("run_id", help="Exact run ID printed by start; prevents signaling a later run.")
        if command in {"approve", "reject"}:
            sub.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "localhost:7233"), namespace="default")
    if args.command == "worker":
        key = os.getenv("AGENTWORKFLOWS_API_KEY")
        if not key:
            parser.error("Set AGENTWORKFLOWS_API_KEY to a team-bound gateway key (local-development-only for Compose).")
        activities = GatewayActivities(os.getenv("AGENTWORKFLOWS_URL", "http://localhost:8080"), key)
        async with Worker(
            client,
            task_queue="research",
            workflows=[ResearchWorkflow],
            activities=[activities.call],
            max_concurrent_activities=2,
            max_concurrent_workflow_tasks=2,
        ):
            await asyncio.Event().wait()
    elif args.command == "start":
        handle = await client.start_workflow(
            ResearchWorkflow.run,
            ResearchRequest(args.topic, args.model),
            id=f"research-{uuid4()}",
            task_queue="research",
            execution_timeout=timedelta(days=8),
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
        print(json.dumps({"workflow_id": handle.id, "run_id": handle.first_execution_run_id}))
    else:
        handle = client.get_workflow_handle(args.workflow_id, run_id=args.run_id)
        if args.command == "status":
            print(json.dumps(await handle.query(ResearchWorkflow.status, rpc_timeout=timedelta(seconds=10)), indent=2))
        elif args.command == "result":
            print(json.dumps(await handle.result(), indent=2))
        else:
            status = await handle.query(ResearchWorkflow.status, rpc_timeout=timedelta(seconds=10))
            if status["stage"] != "awaiting_approval":
                parser.error(f"Run is {status['stage']}; only a draft awaiting approval can be reviewed.")
            await handle.signal(ResearchWorkflow.approve, args=[args.command == "approve", args.reviewer])
            print("Decision submitted. Use result to wait for completion.")


if __name__ == "__main__":
    import asyncio
    import sys

    from temporalio.client import WorkflowFailureError
    from temporalio.service import RPCError

    try:
        asyncio.run(main())
    except RPCError as exc:
        print(
            f"Temporal: {exc}. Check TEMPORAL_ADDRESS, the run IDs, and whether its worker is running.", file=sys.stderr
        )
        sys.exit(1)
    except WorkflowFailureError as exc:
        print(f"Workflow failed: {exc.cause}. Inspect the activity failure in Temporal UI.", file=sys.stderr)
        sys.exit(1)
