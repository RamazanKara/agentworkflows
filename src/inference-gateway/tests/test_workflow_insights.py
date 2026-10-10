# ruff: noqa: F811
from dataclasses import replace
from time import time as clock

import pytest
from app.workflow_insights import FINISHED, outcome, percentile, summarize

from tests.test_teams import auth, start, team_gateway  # noqa: F401

NOW = float(int(clock()))
PATH = "/v1/workflow-insights"


def step(action, step_id, offset, ms=0, tokens=0, cost=0.0, **extra):
    return {
        "action_type": action, "workflow_step_id": step_id, "ts": offset, "latency_ms": ms,
        "workflow_charge": {"tokens": tokens, "cost_usd": cost}, "status_code": 200,
        "record_hash": f"{action}-{step_id}-{offset}", "chain_id": "gateway:test", **extra,
    }


@pytest.fixture
def gateway(team_gateway, monkeypatch):
    client, app = team_gateway
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(
        team, workflows={**team.workflows, "OtherWorkflow": team.workflows["ResearchWorkflow"]}
    )
    monkeypatch.setattr("app.workflow_insights.time", lambda: NOW)
    return client, app, monkeypatch


def launch(gateway, age, steps, *, workflow="ResearchWorkflow", status="COMPLETED", stage="published", role="builder"):
    """Start a run created ``age`` seconds ago with receipts at offsets (seconds) after its creation."""
    client, app, monkeypatch = gateway
    created = NOW - age
    monkeypatch.setattr("app.workflow_operations.time", lambda: created)
    run = start(client, role, workflow=workflow).json()
    execution = app.state.temporal_client.executions[run["workflow_id"]]
    execution.status, execution.stage = status, stage
    for event in steps:
        app.state.storage.append_step("team", run["run_id"], {**event, "ts": created + event["ts"]})
    monkeypatch.setattr("app.workflow_operations.time", lambda: NOW)
    return run


def seed(gateway):
    return {
        "published": launch(gateway, 3600, [
            step("tool_exec", "research", 2, 300, 0, 0.01),
            step("model_call", "analyze", 6, 1400, 1800, 0.02),
            step("model_call", "draft", 10, 1800, 2400, 0.03),
            step("approval", "approval", 610, approved=True),
        ]),
        "rejected": launch(gateway, 7200, [
            step("model_call", "draft", 10, 1500, 1000, 0.02),
            step("approval", "approval", 70, approved=False),
        ], stage="rejected"),
        "failed": launch(gateway, 2 * 86400, [step("tool_exec", "research", 2, 200, 0, 0.01)], status="FAILED"),
        "waiting": launch(gateway, 600, [step("model_call", "draft", 10, 1000, 500, 0.01)], status="RUNNING",
                          stage="awaiting_approval"),
        "old": launch(gateway, 8 * 86400, [step("model_call", "draft", 10, 900, 100, 0.5)]),
        "other": launch(gateway, 1800, [step("model_call", "plan", 5, 700, 100, 0.04)], workflow="OtherWorkflow",
                        status="CANCELED"),
    }


def insights(client, query="", role="viewer"):
    return client.get(f"{PATH}{query}", headers=auth(role))


def test_workflow_insights_summarize_outcomes_time_review_wait_and_cost(gateway):
    client = gateway[0]
    seed(gateway)
    response = insights(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window"] == {"days": 7, "start": NOW - 7 * 86400, "end": NOW}
    assert body["projects"] == ["default", "private"] and body["scanned"] == 5
    assert body["truncated"] is False and body["skipped"] == 0
    research, other = body["workflows"]
    assert research["workflow"] == "ResearchWorkflow" and research["runs"] == 4
    assert research["outcomes"] == {
        "completed": 1, "rejected": 1, "failed": 1, "canceled": 0, "awaiting_approval": 1, "running": 0,
    }
    assert research["completion_rate"] == pytest.approx(1 / 3)
    assert research["median_seconds"] == 70 and research["p95_seconds"] == 610
    assert research["median_review_seconds"] == 330
    assert research["average_cost_usd"] == pytest.approx(0.03)
    assert research["total_cost_usd"] == pytest.approx(0.10)
    assert research["slowest_step"] == {"step_id": "draft", "name": "", "action": "model_call", "median_ms": 1650}
    assert research["costliest_step"] == {
        "step_id": "draft", "name": "", "action": "model_call", "average_cost_usd": pytest.approx(0.025)
    }
    assert other["workflow"] == "OtherWorkflow" and other["outcomes"]["canceled"] == 1
    assert other["completion_rate"] == 0 and other["median_review_seconds"] is None
    month = insights(client, "?days=30").json()["workflows"][0]
    assert month["runs"] == 5 and month["total_cost_usd"] == pytest.approx(0.60)
    day = insights(client, "?days=1").json()["workflows"]
    assert [row["workflow"] for row in day] == ["ResearchWorkflow", "OtherWorkflow"]
    assert day[0]["runs"] == 3 and day[0]["outcomes"]["failed"] == 0 and day[0]["outcomes"]["awaiting_approval"] == 1
    filtered = insights(client, "?workflow=OtherWorkflow").json()
    assert [row["workflow"] for row in filtered["workflows"]] == ["OtherWorkflow"]


def test_insights_respect_roles_projects_teams_and_bounds(gateway):
    client, _, monkeypatch = gateway
    seed(gateway)
    private = launch(gateway, 100, [step("model_call", "draft", 3, 100, 10, 0.001)], role="project")
    assert insights(client).status_code == 200
    assert client.get(PATH).status_code == 401
    only = insights(client, role="project").json()
    assert only["projects"] == ["private"] and only["scanned"] == 1
    assert only["workflows"][0]["runs"] == 1 and private["project"] == "private"
    assert insights(client, "?project=private").json()["projects"] == ["private"]
    assert insights(client, "?project=missing").status_code == 404
    assert insights(client, "?days=31").status_code == 422 and insights(client, "?days=0").status_code == 422
    assert insights(client, role="other").json()["workflows"] == []
    monkeypatch.setattr("app.workflow_insights.SCAN_LIMIT", 2)
    capped = insights(client).json()
    assert capped["truncated"] is True and capped["scanned"] == 2
    assert sum(row["runs"] for row in capped["workflows"]) == 2


def test_run_detail_explains_where_time_and_money_went(gateway):
    client = gateway[0]
    runs = seed(gateway)
    summary = client.get(f"/v1/workflow-runs/{runs['published']['run_id']}", headers=auth("viewer")).json()["summary"]
    assert summary["elapsed_seconds"] == 610 and summary["review_seconds"] == 600 and summary["review_open"] is False
    assert (summary["model_calls"], summary["tool_calls"]) == (2, 1)
    assert (summary["model_ms"], summary["tool_ms"]) == (3200, 300)
    assert summary["tokens"] == 4200 and summary["cost_usd"] == pytest.approx(0.06)
    assert [row["step_id"] for row in summary["by_step"]] == ["research", "analyze", "draft"]
    named = summarize(0, [{"action": "tool_exec", "step_id": "1", "tool": "research", "model": "", "timestamp": 1,
                           "duration_ms": 5, "tokens": 0, "cost_usd": 0}])
    assert named["by_step"][0]["name"] == "research"
    assert summary["slowest_step"]["step_id"] == "draft" and summary["costliest_step"]["step_id"] == "draft"
    waiting = client.get(f"/v1/workflow-runs/{runs['waiting']['run_id']}", headers=auth("viewer")).json()["summary"]
    assert waiting["review_open"] is True and waiting["review_seconds"] == 590
    assert waiting["elapsed_seconds"] == 600
    # Listings stay light: no receipts, so no summary.
    listed = client.get("/v1/workflow-runs?limit=5", headers=auth("viewer")).json()["runs"]
    assert listed and all("summary" not in row for row in listed)


def test_summary_handles_empty_and_operation_only_runs():
    empty = summarize(100, [])
    assert empty["elapsed_seconds"] is None and empty["by_step"] == [] and empty["slowest_step"] is None
    operations = [
        {"action": "cancel", "step_id": "cancel", "timestamp": 130, "duration_ms": 0, "tokens": 0, "cost_usd": 0}
    ]
    assert summarize(100, operations)["elapsed_seconds"] == 30
    assert summarize(100, operations)["model_calls"] == 0
    assert summarize(100, operations, now=500, waiting=True)["review_seconds"] is None


def test_outcome_and_percentile_helpers():
    assert outcome({"status": "completed", "outcome": "rejected"}) == "rejected"
    assert outcome({"status": "completed"}) == "completed"
    assert outcome({"status": "terminated"}) == "canceled" and outcome({"status": "timed_out"}) == "failed"
    assert outcome({"status": "running", "progress": {"stage": "awaiting_approval"}}) == "awaiting_approval"
    assert outcome({"status": "continued_as_new"}) == "running"
    assert set(FINISHED) == {"completed", "rejected", "failed", "canceled"}
    assert percentile([], 0.95) is None and percentile([3, 1, 2], 0.5) == 2 and percentile([1], 0.95) == 1
