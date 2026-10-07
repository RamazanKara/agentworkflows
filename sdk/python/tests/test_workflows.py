import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from agentworkflows.activities import GatewayActivities
from agentworkflows.examples.code_review import CodeReviewWorkflow
from agentworkflows.examples.research import ResearchWorkflow
from agentworkflows.examples.support_triage import SupportTriageWorkflow
from agentworkflows.workflows import Budget, Call, WorkflowGateway
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner


@pytest.mark.parametrize("workflow_class", [ResearchWorkflow, SupportTriageWorkflow, CodeReviewWorkflow])
def test_example_loads_in_temporal_sandbox(workflow_class):
    from temporalio.workflow import _Definition

    async def validate():
        SandboxedWorkflowRunner().prepare_workflow(_Definition.must_from_class(workflow_class))

    asyncio.run(validate())


def test_sdk_schedules_temporal_activities_with_budgets_and_timeouts(monkeypatch):
    execute = AsyncMock(return_value={"result": "published"})
    monkeypatch.setattr("agentworkflows.workflows.workflow.execute_activity", execute)
    gateway = WorkflowGateway(Budget(300, 0.5))
    asyncio.run(gateway.model([{"role": "user", "content": "hello"}], model="approved"))
    args, options = execute.call_args
    assert args[0] == "agentworkflows.call"
    assert args[1].budget == Budget(300, 0.5)
    assert options["start_to_close_timeout"].total_seconds() == 180
    assert options["schedule_to_close_timeout"].total_seconds() == 900
    assert options["retry_policy"].maximum_attempts == 5
    assert asyncio.run(gateway.tool("publish", {"body": "ok"})) == "published"


@pytest.mark.parametrize(
    "status,reason,retryable",
    [
        (403, "workflow_token_budget_exceeded", False),
        (400, "tool_not_allowed", False),
        (503, "workflow_store_unavailable", True),
    ],
)
def test_activity_policy_errors_do_not_retry(monkeypatch, status, reason, retryable):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={})
        return httpx.Response(status, json={"detail": {"reason": reason, "message": "fixture"}})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "agentworkflows.activities.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(respond), **kw),
    )
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info, workflow_run_id="ebf9363a-7912-4a62-89b4-b0a28c169731", activity_id="draft"
    )
    with pytest.raises(ApplicationError) as error:
        asyncio.run(environment.run(GatewayActivities("http://gateway", "team-key").call, Call("model", {})))
    assert error.value.non_retryable is not retryable
    assert requests[1].headers["X-Workflow-Step-ID"] == "draft"
    assert "team-key" not in str(error.value)


def test_approval_only_applies_to_one_waiting_draft():
    workflow = ResearchWorkflow()
    workflow.approve(True, "too-early")
    assert workflow.decision is None
    workflow.stage = "awaiting_approval"
    workflow.approve(False, "reviewer")
    workflow.approve(True, "late-reviewer")
    assert workflow.decision is False
    assert workflow.reviewer == "reviewer"


def test_review_update_accepts_one_waiting_decision():
    workflow = ResearchWorkflow()
    assert workflow.review(True, "too-early") is False
    workflow.stage = "awaiting_approval"
    assert workflow.review(False, "verified-reviewer") is True
    assert workflow.review(True, "another-reviewer") is False
    assert workflow.reviewer == "verified-reviewer" and workflow.decision is False


def test_text_uses_governed_activity_and_gateway_default(monkeypatch):
    execute = AsyncMock(return_value={"choices": [{"message": {"content": "answer"}}]})
    monkeypatch.setattr("agentworkflows.workflows.workflow.execute_activity", execute)
    assert asyncio.run(WorkflowGateway(Budget(300, 0.5)).text("hello")) == "answer"
    call = execute.call_args.args[1]
    assert call.kind == "model" and call.budget == Budget(300, 0.5)
    assert call.payload == {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 512}


@pytest.mark.parametrize("approved", [True, False])
def test_research_publishes_only_after_review(monkeypatch, approved):
    from types import SimpleNamespace

    from agentworkflows.examples.research import ResearchRequest

    instance = ResearchWorkflow()
    tool = AsyncMock(return_value="fixture")
    monkeypatch.setattr(WorkflowGateway, "tool", tool)
    monkeypatch.setattr(WorkflowGateway, "text", AsyncMock(return_value="draft"))
    monkeypatch.setattr("agentworkflows.workflows.workflow.info", lambda: SimpleNamespace(run_id="run"))

    async def review(condition, *, timeout):
        assert instance.stage == "awaiting_approval" and instance.draft == "draft"
        assert tool.await_count == 1
        assert timeout.days == 7 and not condition()
        assert instance.review(approved, "reviewer")
        assert condition()

    monkeypatch.setattr("agentworkflows.workflows.workflow.wait_condition", review)
    result = asyncio.run(instance.run(ResearchRequest("topic")))
    assert result["status"] == ("published" if approved else "rejected")
    assert tool.await_count == (2 if approved else 1)


def test_approval_timeout_is_actionable_and_not_retryable(monkeypatch):
    monkeypatch.setattr("agentworkflows.workflows.workflow.wait_condition", AsyncMock(side_effect=TimeoutError))
    with pytest.raises(ApplicationError, match="start a new review") as exc:
        asyncio.run(ResearchWorkflow().approval("draft"))
    assert exc.value.non_retryable


def test_worker_requires_key_before_connecting(monkeypatch):
    from agentworkflows.worker import run_worker

    monkeypatch.delenv("AGENTWORKFLOWS_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="demo-worker"):
        run_worker([ResearchWorkflow])
