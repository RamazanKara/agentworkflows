# ruff: noqa: F811  (shared gateway fixtures are imported, then requested by name)
import asyncio
import json
import logging
from dataclasses import replace
from time import time

import pytest
from app.policy import TeamNotifications
from app.workflow_operations import refresh_workflow_metrics

from tests.test_team_settings import WORKFLOW, initialized_run, update
from tests.test_teams import auth, start, team_gateway  # noqa: F401

LIST = "/v1/team/approval-escalations"
PATH = "/v1/team/workflows/ResearchWorkflow/approval-escalation"


def escalate(client, revision, **body):
    return client.put(PATH, headers={**auth("admin"), "If-Match": str(revision)}, json={"after_seconds": 3600, **body})


@pytest.fixture
def gateway(team_gateway, monkeypatch):
    client, app = team_gateway
    clock = {"now": time()}
    monkeypatch.setattr("app.workflow_escalation.time", lambda: clock["now"])
    return client, app, clock


def admin_only(client):
    """Make the workflow need an admin review, so escalation has someone to add."""
    assert update(client, {WORKFLOW + "approver_role": "admin"}).status_code == 200


def waiting_run(client):
    run, path, _ = initialized_run(client)
    gate = client.post(path + "/approval-waiting", headers=auth("worker"), json={"policy_version": 1})
    assert gate.status_code == 200 and gate.json()["queued"] is True
    return run, path


def receipts(client, path):
    return [step["receipt"] for step in client.get(path, headers=auth("admin")).json()["timeline"]]


def test_configuration_is_admin_only_revision_checked_validated_and_audited(gateway, caplog):
    client, app, _ = gateway
    for role in ("builder", "approver", "viewer", "project"):
        assert client.get(LIST, headers=auth(role)).status_code == 403
        assert client.put(PATH, headers={**auth(role), "If-Match": "0"}, json={"after_seconds": 60}).status_code == 403
    empty = {"revision": 0, "escalations": [], "available_channels": []}
    assert client.get(LIST, headers=auth("admin")).json() == empty
    assert escalate(client, 1).status_code == 409
    missing = client.put(
        PATH.replace("ResearchWorkflow", "Unknown"),
        headers={**auth("admin"), "If-Match": "0"},
        json={"after_seconds": 60},
    )
    assert missing.status_code == 404 and missing.json()["detail"]["reason"] == "workflow_not_found"
    for body in (
        {"after_seconds": 59},
        {"after_seconds": 604741},
        {"after_seconds": "60"},
        {"approver_role": "admin"},
        {"reviewers": [" "]},
        {"channels": ["sms"]},
        {"unknown": 1},
    ):
        assert escalate(client, 0, **body).status_code == 422, body
    unavailable = escalate(client, 0, channels=["slack"])
    assert unavailable.status_code == 422 and unavailable.json()["detail"]["fields"][0]["field"] == "channels"
    assert update(client, {WORKFLOW + "approval_timeout_seconds": 3600}).status_code == 200
    late = escalate(client, 1)
    assert late.status_code == 422 and late.json()["detail"]["reason"] == "approval_escalation_invalid"
    caplog.set_level(logging.INFO)
    saved = escalate(client, 1, after_seconds=1800, approver_role="approver", reviewers=["maya", "maya", " leo "])
    assert saved.status_code == 200, saved.text
    assert saved.headers["ETag"] == '"2"'
    body = saved.json()
    assert body["revision"] == 2
    assert body["escalation"]["reviewers"] == ["maya", "leo"] and body["escalation"]["effective"] is True
    assert body["escalation"]["updated_by"] == "admin" and body["escalation"]["channels"] is None
    assert (
        escalate(client, 2, after_seconds=1800, approver_role="approver", reviewers=["maya", "leo"]).json()["revision"]
        == 2
    )
    events = [json.loads(row.message) for row in caplog.records if row.name == "agentworkflows.audit"]
    changed = [event for event in events if event["action_type"] == "approval_escalation_changed"]
    assert len(changed) == 1 and changed[0]["escalation"]["after_seconds"] == 1800
    policies = client.get("/v1/workflow-policies", headers=auth("viewer")).json()["workflows"]
    assert policies["ResearchWorkflow"]["approvalEscalation"]["after_seconds"] == 1800
    # A shorter timeout later on makes the escalation moot; the listing says so.
    assert update(client, {WORKFLOW + "approval_timeout_seconds": 600}, 2).status_code == 200
    assert client.get(LIST, headers=auth("admin")).json()["escalations"][0]["effective"] is False
    assert client.delete(PATH, headers={**auth("admin"), "If-Match": "3"}).json() == {
        "revision": 4,
        "removed": "ResearchWorkflow",
    }
    assert client.delete(PATH, headers={**auth("admin"), "If-Match": "4"}).status_code == 404
    assert app.state.storage.get_settings("other") is None


def test_review_opens_to_approvers_only_after_the_deadline_with_receipts(gateway):
    client, app, clock = gateway
    admin_only(client)
    assert escalate(client, 1, approver_role="approver").status_code == 200
    _, path = waiting_run(client)
    early = client.post(path + "/approve", headers=auth("approver"), json={})
    assert early.status_code == 403
    assert "opens to you at" in early.json()["detail"]["message"]
    detail = client.get(path, headers=auth("approver")).json()
    state = detail["approval_escalation"]
    assert state["after_seconds"] == 3600 and state["escalated"] is False and state["recorded_at"] is None
    assert state["escalates_at"] == pytest.approx(state["waiting_since"] + 3600)
    assert detail["approval_access"] == {"eligible": False, "via": None, "eligible_at": state["escalates_at"]}
    assert client.get(path, headers=auth("admin")).json()["approval_access"]["via"] == "role"
    assert client.get(path, headers=auth("viewer")).json()["approval_access"]["eligible"] is False
    clock["now"] = state["escalates_at"] + 1
    listed = client.get("/v1/workflow-runs", headers=auth("approver")).json()["runs"][0]
    assert listed["approval_escalation"]["escalated"] is True and listed["approval_escalation"]["recorded_at"] is None
    assert listed["approval_access"] == {"eligible": True, "via": "escalation", "eligible_at": None}
    vote = client.post(path + "/approve", headers=auth("approver"), json={})
    assert vote.status_code == 200 and vote.json()["reviewer"] == "approver"
    assert client.post(path + "/approve", headers=auth("approver"), json={}).status_code == 200
    steps = receipts(client, path)
    actions = [step["action_type"] for step in steps]
    assert actions.index("approval_escalated") < actions.index("approval")
    escalated = next(step for step in steps if step["action_type"] == "approval_escalated")
    assert escalated["principal"] == {"auth": "policy", "key_id": "approval-escalation"}
    assert escalated["approver_role"] == "approver" and escalated["after_seconds"] == 3600
    approval = next(step for step in steps if step["action_type"] == "approval")
    assert approval["eligibility"] == "escalation" and approval["principal"]["key_id"] == "approver"
    assert actions.count("approval") == 1
    asyncio.run(refresh_workflow_metrics(app))
    assert [step["action_type"] for step in receipts(client, path)].count("approval_escalated") == 1
    assert client.get(path, headers=auth("admin")).json()["approval_escalation"]["recorded_at"] == pytest.approx(
        clock["now"]
    )


def test_named_reviewers_and_runs_keep_the_escalation_they_started_with(gateway):
    client, _, clock = gateway
    admin_only(client)
    _, before = waiting_run(client)
    assert escalate(client, 1, reviewers=["someone-else"]).status_code == 200
    _, others = waiting_run(client)
    assert escalate(client, 2, reviewers=["approver"]).status_code == 200
    _, named = waiting_run(client)
    clock["now"] += 3601
    assert "approval_escalation" not in client.get(before, headers=auth("admin")).json()
    assert client.post(before + "/approve", headers=auth("approver"), json={}).status_code == 403
    assert client.post(others + "/approve", headers=auth("approver"), json={}).status_code == 403
    assert client.post(named + "/approve", headers=auth("approver"), json={}).status_code == 200
    assert client.post(before + "/approve", headers=auth("admin"), json={}).status_code == 200


def test_monitor_escalates_once_and_notifies_the_chosen_destination(gateway, monkeypatch):
    client, app, clock = gateway
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(
        team,
        notifications=TeamNotifications.model_validate(
            {"consoleUrl": "https://console.test/", "slackWebhookEnv": "SLACK", "webhookEnv": "HOOK"}
        ),
    )
    rules = {"events": ["failed"], "channels": ["slack"], "budget_threshold": 0.8, "slow_step_ms": 1000}
    assert client.put("/v1/team/alert-rules", headers={**auth("admin"), "If-Match": "0"}, json=rules).status_code == 200
    assert escalate(client, 1, approver_role="approver", reviewers=["leo"], channels=["webhook"]).status_code == 200
    sent = []

    async def send(config, channel, payload):
        sent.append((channel, payload))

    monkeypatch.setattr("app.workflow_notifications.send_notification", send)
    run, path = waiting_run(client)
    asyncio.run(refresh_workflow_metrics(app))
    assert sent == [] and "approval_escalated" not in [step["action_type"] for step in receipts(client, path)]
    clock["now"] += 3601
    asyncio.run(refresh_workflow_metrics(app))
    asyncio.run(refresh_workflow_metrics(app))
    assert [(channel, payload["event"]) for channel, payload in sent] == [("webhook", "approval_escalated")]
    payload = sent[0][1]
    assert payload["escalation"]["approver_role"] == "approver" and payload["escalation"]["reviewers"] == ["leo"]
    assert payload["run_id"] == run["run_id"]
    assert [step["action_type"] for step in receipts(client, path)].count("approval_escalated") == 1


@pytest.mark.parametrize("finished", ["published", "silent"])
def test_finished_reviews_and_silent_escalations_send_nothing(gateway, monkeypatch, finished):
    client, app, clock = gateway
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(
        team,
        notifications=TeamNotifications.model_validate({"consoleUrl": "https://console.test/", "webhookEnv": "HOOK"}),
    )
    channels = [] if finished == "silent" else None
    assert escalate(client, 0, channels=channels).status_code == 200
    sent = []

    async def send(config, channel, payload):
        sent.append(payload["event"])

    monkeypatch.setattr("app.workflow_notifications.send_notification", send)
    run, path = waiting_run(client)
    if finished == "published":
        app.state.temporal_client.executions[run["workflow_id"]].stage = "published"
    clock["now"] += 3601
    asyncio.run(refresh_workflow_metrics(app))
    assert "approval_escalated" not in sent
    escalated = "approval_escalated" in [step["action_type"] for step in receipts(client, path)]
    assert escalated is (finished == "silent")


def test_removing_a_registered_workflow_removes_its_escalation(gateway):
    client, app, _ = gateway
    body = {"allowed_models": ["primary"], "approver_role": "admin", "cost_limit_usd": 0.5}
    assert (
        client.put("/v1/team/workflows/Triage", headers={**auth("admin"), "If-Match": "0"}, json=body).status_code
        == 200
    )
    escalation = client.put(
        "/v1/team/workflows/Triage/approval-escalation",
        headers={**auth("admin"), "If-Match": "1"},
        json={"after_seconds": 600},
    )
    assert escalation.status_code == 200
    assert client.delete("/v1/team/workflows/Triage", headers={**auth("admin"), "If-Match": "2"}).status_code == 200
    assert "Triage" not in app.state.storage.get_settings("team")["approval_escalations"]
    assert client.get(LIST, headers=auth("admin")).json()["escalations"] == []
