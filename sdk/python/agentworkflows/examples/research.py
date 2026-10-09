"""Research -> draft -> human approval -> publish through governed activities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from temporalio import workflow

from agentworkflows.workflows import ApprovalWorkflow, Budget, WorkflowGateway, input_schema


@dataclass
class ResearchRequest:
    topic: str
    model: str = "demo-openai"
    token_limit: int = 10000
    cost_limit_usd: float = 5.0


@input_schema(
    {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "A question or subject, in a sentence.",
                "examples": ["How should our team evaluate AI agents?"],
                "minLength": 1,
                "pattern": "\\S",
            },
            "model": {
                "type": "string",
                "default": "demo-openai",
                "minLength": 1,
                "pattern": "\\S",
            },
            "token_limit": {
                "type": "integer",
                "default": 10000,
                "minimum": 1,
                "maximum": 1000000000,
            },
            "cost_limit_usd": {
                "type": "number",
                "default": 5,
                "exclusiveMinimum": 0,
                "maximum": 1000000,
            },
        },
        "required": ["topic"],
        "additionalProperties": False,
    }
)
@workflow.defn
class ResearchWorkflow(ApprovalWorkflow):
    @workflow.run
    async def run(self, request: ResearchRequest) -> dict[str, Any]:
        gateway = WorkflowGateway(Budget(request.token_limit, request.cost_limit_usd))
        sources = await gateway.tool("research", {"query": request.topic})
        research = await gateway.text(
            f"Summarize these sources on {request.topic}; cite URLs and flag uncertainty.\n{sources}",
            model=request.model,
        )
        self.stage = "draft"
        draft = await gateway.text(
            f"Write a concise team briefing with citations from this research:\n{research}", model=request.model
        )
        if not await self.approval(draft):
            return {"status": self.stage, "reviewer": self.reviewer}
        self.stage = "publish"
        published = await gateway.tool("publish", {"title": request.topic, "body": draft, "reviewer": self.reviewer})
        self.stage = "published"
        return {"status": self.stage, "publication": published, "run_id": workflow.info().run_id}


async def main() -> None:
    import argparse
    import json
    import os
    from datetime import timedelta
    from uuid import uuid4

    from temporalio.client import Client
    from temporalio.common import WorkflowIDReusePolicy

    from agentworkflows.examples.code_review import CodeReviewWorkflow
    from agentworkflows.examples.document_qa import DocumentQAWorkflow
    from agentworkflows.examples.frameworks import AGENTS, CodeWorkflow, FrameworkWorkflow
    from agentworkflows.examples.incident_summary import IncidentSummaryWorkflow
    from agentworkflows.examples.meeting_actions import MeetingActionsWorkflow
    from agentworkflows.examples.release_notes import ReleaseNotesWorkflow
    from agentworkflows.examples.security_questionnaire import SecurityQuestionnaireWorkflow
    from agentworkflows.examples.support_triage import SupportTriageWorkflow
    from agentworkflows.examples.weekly_report import WeeklyReportWorkflow
    from agentworkflows.worker import serve

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
    if args.command == "worker":
        from agentworkflows.examples.triggered import DailyReportWorkflow, GitHubIssueTriageWorkflow

        await serve(
            [
                ResearchWorkflow,
                ReleaseNotesWorkflow,
                MeetingActionsWorkflow,
                SecurityQuestionnaireWorkflow,
                SupportTriageWorkflow,
                CodeReviewWorkflow,
                WeeklyReportWorkflow,
                IncidentSummaryWorkflow,
                DocumentQAWorkflow,
                FrameworkWorkflow,
                CodeWorkflow,
                DailyReportWorkflow,
                GitHubIssueTriageWorkflow,
            ],
            agents=AGENTS,
        )
        return
    client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "localhost:7233"), namespace="default")
    if args.command == "start":
        handle = await client.start_workflow(
            ResearchWorkflow.run,
            ResearchRequest(args.topic, args.model),
            id=f"{os.getenv('AGENTWORKFLOWS_TEAM', 'demo')}/default/{uuid4()}",
            task_queue=os.getenv("TEMPORAL_TASK_QUEUE", f"{os.getenv('AGENTWORKFLOWS_TEAM', 'demo')}-workflows"),
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
