import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from agentworkflows.activities import GatewayActivities
from agentworkflows.examples.code_review import CodeReviewWorkflow
from agentworkflows.examples.document_qa import DocumentQARequest, DocumentQAWorkflow
from agentworkflows.examples.frameworks import FrameworkWorkflow
from agentworkflows.examples.incident_summary import IncidentSummaryRequest, IncidentSummaryWorkflow
from agentworkflows.examples.research import ResearchWorkflow
from agentworkflows.examples.support_triage import SupportTriageWorkflow
from agentworkflows.examples.triggered import DailyReportWorkflow, GitHubIssueTriageWorkflow
from agentworkflows.examples.weekly_report import WeeklyReportRequest, WeeklyReportWorkflow
from agentworkflows.triggers import ScheduledTrigger
from agentworkflows.workflows import Budget, Call, WorkflowGateway, input_schema
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner


def test_input_schema_preserves_temporal_workflow_definition(monkeypatch):
    from temporalio.workflow import _Definition

    schema = {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}
    original = _Definition.must_from_class(ResearchWorkflow)
    monkeypatch.setattr(ResearchWorkflow, "input_schema", ResearchWorkflow.input_schema)
    assert input_schema(schema)(ResearchWorkflow) is ResearchWorkflow
    assert _Definition.must_from_class(ResearchWorkflow) is original
    assert ResearchWorkflow.input_schema is schema


@pytest.mark.parametrize("workflow_class, field", [
    (ResearchWorkflow, "topic"), (CodeReviewWorkflow, "diff"), (SupportTriageWorkflow, "ticket"),
    (WeeklyReportWorkflow, "period"), (IncidentSummaryWorkflow, "incident_id"), (DocumentQAWorkflow, "question"),
    (FrameworkWorkflow, "topic"),
])
def test_template_declares_serializable_input_schema(workflow_class, field):
    schema = json.loads(json.dumps(workflow_class.input_schema))
    assert schema["type"] == "object"
    assert schema["required"] == [field]
    assert schema["properties"][field]["type"] == "string"


@pytest.mark.parametrize(
    "workflow_class",
    [
        ResearchWorkflow,
        SupportTriageWorkflow,
        CodeReviewWorkflow,
        WeeklyReportWorkflow,
        IncidentSummaryWorkflow,
        DocumentQAWorkflow,
        DailyReportWorkflow,
        GitHubIssueTriageWorkflow,
        ScheduledTrigger,
    ],
)
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
    event = AsyncMock(return_value={"queued": True})
    monkeypatch.setattr(WorkflowGateway, "_call", event)
    monkeypatch.setattr("agentworkflows.workflows.workflow.patched", lambda name: name == "approval-notification-v1")
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
    assert event.call_args.args[0].kind == "approval_waiting"
    assert tool.await_count == (2 if approved else 1)


def test_approval_timeout_is_actionable_and_not_retryable(monkeypatch):
    monkeypatch.setattr("agentworkflows.workflows.workflow.patched", lambda _: False)
    monkeypatch.setattr("agentworkflows.workflows.workflow.wait_condition", AsyncMock(side_effect=TimeoutError))
    with pytest.raises(ApplicationError, match="start a new review") as exc:
        asyncio.run(ResearchWorkflow().approval("draft"))
    assert exc.value.non_retryable


@pytest.mark.parametrize("reject", [False, True])
def test_quorum_counts_distinct_reviewers_and_rejection_ends_gate(monkeypatch, reject):
    from datetime import UTC, datetime

    instance = ResearchWorkflow()
    monkeypatch.setattr("agentworkflows.workflows.workflow.patched", lambda _: True)
    monkeypatch.setattr("agentworkflows.workflows.workflow.now", lambda: datetime(2026, 10, 9, tzinfo=UTC))
    event = AsyncMock(return_value={"policy_version": 1, "required_approvals": 2, "approval_timeout_seconds": 60})
    monkeypatch.setattr(WorkflowGateway, "_call", event)

    async def review(condition, *, timeout):
        assert timeout.total_seconds() == 60
        assert instance.review(True, " reviewer-a ")
        assert not condition()
        assert not instance.review(True, "reviewer-a")
        assert not instance.review(False, "reviewer-a")
        assert not instance.review(True, " reviewer-a ")
        assert not instance.review(True, " ")
        assert instance.review(not reject, "reviewer-b")
        assert condition()
        assert not instance.review(True, "reviewer-c")

    monkeypatch.setattr("agentworkflows.workflows.workflow.wait_condition", review)
    assert asyncio.run(instance.approval("draft")) is not reject
    assert instance.approved_by == (["reviewer-a"] if reject else ["reviewer-a", "reviewer-b"])
    assert event.call_args.args[0].payload == {"policy_version": 1}


def test_quorum_deadline_rejects_late_votes_and_sets_expired(monkeypatch):
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 10, 9, tzinfo=UTC)
    instance = ResearchWorkflow()
    monkeypatch.setattr("agentworkflows.workflows.workflow.patched", lambda _: True)
    monkeypatch.setattr("agentworkflows.workflows.workflow.now", lambda: now)
    monkeypatch.setattr(WorkflowGateway, "_call", AsyncMock(return_value={
        "policy_version": 1, "required_approvals": 2, "approval_timeout_seconds": 60,
    }))

    async def wait(condition, *, timeout):
        nonlocal now
        assert instance.review(True, "first")
        now += timedelta(seconds=60)
        assert not instance.review(True, "late")
        assert not instance.review(False, "late")
        assert not condition()
        raise TimeoutError

    monkeypatch.setattr("agentworkflows.workflows.workflow.wait_condition", wait)
    with pytest.raises(ApplicationError, match="Approval expired"):
        asyncio.run(instance.approval("draft"))
    assert instance.stage == "expired"
    assert instance.approved_by == ["first"]


def test_policy_gate_rejects_early_votes_and_honors_automatic_approval(monkeypatch):
    from datetime import UTC, datetime

    instance = ResearchWorkflow()
    monkeypatch.setattr("agentworkflows.workflows.workflow.patched", lambda _: True)
    monkeypatch.setattr("agentworkflows.workflows.workflow.now", lambda: datetime(2026, 10, 9, tzinfo=UTC))

    async def configure(self, call):
        assert not instance.review(True, "early")
        return {"policy_version": 1, "required_approvals": 2, "approval_required": False}

    async def wait(condition, **kwargs):
        assert condition()

    monkeypatch.setattr(WorkflowGateway, "_call", configure)
    monkeypatch.setattr("agentworkflows.workflows.workflow.wait_condition", wait)
    assert asyncio.run(instance.approval("draft"))
    assert instance.reviewer == "team policy" and instance.approved_by == []


def test_new_worker_requires_versioned_gateway(monkeypatch):
    monkeypatch.setattr("agentworkflows.workflows.workflow.patched", lambda _: True)
    monkeypatch.setattr(WorkflowGateway, "_call", AsyncMock(return_value={"queued": True}))
    with pytest.raises(ApplicationError, match="Upgrade the gateway"):
        asyncio.run(ResearchWorkflow().approval("draft"))


def test_approval_activity_requests_policy_without_initializing_budget(monkeypatch):
    requests = []
    gate = {"policy_version": 1, "required_approvals": 2, "approval_timeout_seconds": 60, "queued": True}

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=gate)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "agentworkflows.activities.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(respond), **kw),
    )
    environment = ActivityEnvironment()
    environment.info = replace(environment.info, workflow_run_id="ebf9363a-7912-4a62-89b4-b0a28c169731")
    result = asyncio.run(environment.run(
        GatewayActivities("http://gateway", "worker").call, Call("approval_waiting", {"policy_version": 1}),
    ))
    assert result == gate and len(requests) == 1
    assert requests[0].url.path.endswith("/approval-waiting")
    assert json.loads(requests[0].content) == {"policy_version": 1}


def test_worker_requires_key_before_connecting(monkeypatch):
    from agentworkflows.worker import run_worker

    monkeypatch.delenv("AGENTWORKFLOWS_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="demo-worker"):
        run_worker([ResearchWorkflow])


def test_weekly_report_combines_three_governed_sources(monkeypatch):
    sources = [{"id": name, "text": name + " snapshot"} for name in ("changes", "support", "incidents")]
    tool = AsyncMock(side_effect=sources)
    text = AsyncMock(return_value="weekly draft")
    monkeypatch.setattr(WorkflowGateway, "tool", tool)
    monkeypatch.setattr(WorkflowGateway, "text", text)
    result = asyncio.run(WeeklyReportWorkflow().run(WeeklyReportRequest("last week")))
    assert result == {"period": "last week", "report": "weekly draft", "sources": sources}
    assert [call.args for call in tool.await_args_list] == [
        ("report_source", {"source": source["id"], "period": "last week"}) for source in sources
    ]
    assert all(source["text"] in text.call_args.args[0] for source in sources)


def test_incident_summary_keeps_the_log_evidence(monkeypatch):
    logs = {"lines": ["L1 09:00 UTC errors increased", "L2 09:20 UTC baseline restored"]}
    tool = AsyncMock(return_value=logs)
    text = AsyncMock(return_value="summary [L1] [L2]")
    monkeypatch.setattr(WorkflowGateway, "tool", tool)
    monkeypatch.setattr(WorkflowGateway, "text", text)
    result = asyncio.run(IncidentSummaryWorkflow().run(IncidentSummaryRequest("INC-1042")))
    tool.assert_awaited_once_with("incident_logs", {"incident_id": "INC-1042"})
    assert result["logs"] == logs and result["summary"] == "summary [L1] [L2]"
    assert all(line in text.call_args.args[0] for line in logs["lines"])


def test_document_answer_citations_preserve_retrieved_evidence(monkeypatch):
    source = {"id": "S1", "url": "https://example.test/doc", "title": "Handbook", "text": "Seven days."}
    tool = AsyncMock(return_value=[source, {**source, "id": "S2"}])
    monkeypatch.setattr(WorkflowGateway, "tool", tool)
    monkeypatch.setattr(
        WorkflowGateway,
        "text",
        AsyncMock(return_value=json.dumps({"answer": "Seven days. [S1]", "citation_ids": ["S1"]})),
    )
    result = asyncio.run(DocumentQAWorkflow().run(DocumentQARequest("When does approval expire?")))
    assert result == {"answer": "Seven days. [S1]", "citations": [source]}
    tool.assert_awaited_once_with("search_documents", {"query": "When does approval expire?"})


@pytest.mark.parametrize(
    "answer",
    [
        "not JSON",
        "[]",
        '{"answer": "", "citation_ids": []}',
        '{"answer": "text", "citation_ids": "S1"}',
        '{"answer": "text", "citation_ids": [1]}',
        '{"answer": "text [S9]", "citation_ids": ["S9"]}',
        '{"answer": "text [S9]", "citation_ids": ["S1"]}',
        '{"answer": "text without citation", "citation_ids": ["S1"]}',
    ],
)
def test_document_answer_rejects_invalid_or_fabricated_citations(monkeypatch, answer):
    monkeypatch.setattr(WorkflowGateway, "tool", AsyncMock(return_value=[{"id": "S1", "text": "evidence"}]))
    monkeypatch.setattr(WorkflowGateway, "text", AsyncMock(return_value=answer))
    with pytest.raises(ApplicationError) as error:
        asyncio.run(DocumentQAWorkflow().run(DocumentQARequest("question")))
    assert error.value.non_retryable


@pytest.mark.parametrize("sources", [[], [{"id": "S1", "text": "unrelated"}]])
def test_document_answer_abstains_without_evidence(monkeypatch, sources):
    monkeypatch.setattr(WorkflowGateway, "tool", AsyncMock(return_value=sources))
    text = AsyncMock(return_value='{"answer": "Unsupported speculation", "citation_ids": []}')
    monkeypatch.setattr(WorkflowGateway, "text", text)
    result = asyncio.run(DocumentQAWorkflow().run(DocumentQARequest("unanswerable")))
    assert result == {"answer": "I don't know from the supplied documents.", "citations": []}
    assert text.await_count == (1 if sources else 0)
