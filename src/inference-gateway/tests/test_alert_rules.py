# ruff: noqa: F811
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

from app.policy import TeamNotifications
from app.workflow_notifications import notify_run
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError

from tests.test_teams import auth, start, team_gateway  # noqa: F401
from tests.test_workflow_triggers import internal


def test_alert_rules_validate_destinations_revisions_and_roles(team_gateway):
    client, app = team_gateway
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(
        team, notifications=TeamNotifications(consoleUrl="https://console.test/console/", webhookEnv="TEAM_WEBHOOK")
    )
    path = "/v1/team/alert-rules"
    rules = {"events": ["failed", "slow_step"], "channels": ["webhook"], "budget_threshold": 0.9, "slow_step_ms": 1000}
    assert client.get(path, headers=auth("viewer")).status_code == 403
    assert client.put(path, headers={**auth("builder"), "If-Match": "0"}, json=rules).status_code == 403
    saved = client.put(path, headers={**auth("admin"), "If-Match": "0"}, json=rules)
    assert saved.status_code == 200, saved.text
    assert saved.json() == {**rules, "revision": 1, "available_channels": ["webhook"]}
    assert client.put(path, headers={**auth("admin"), "If-Match": "0"}, json=rules).status_code == 409
    assert (
        client.put(path, headers={**auth("admin"), "If-Match": "1"}, json={**rules, "channels": ["slack"]}).status_code
        == 422
    )
    assert (
        client.put(path, headers={**auth("admin"), "If-Match": "1"}, json={**rules, "slow_step_ms": 0}).status_code
        == 422
    )
    assert app.state.storage.get_settings("other") is None


def test_selected_events_channels_and_slow_steps_deliver_once(team_gateway, monkeypatch):
    client, app = team_gateway
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(
        team,
        notifications=TeamNotifications(
            consoleUrl="https://console.test/console/", webhookEnv="TEAM_WEBHOOK", slackWebhookEnv="SLACK"
        ),
    )
    rules = {"events": ["slow_step"], "channels": ["webhook"], "budget_threshold": 0.9, "slow_step_ms": 1000}
    client.put("/v1/team/alert-rules", headers={**auth("admin"), "If-Match": "0"}, json=rules)
    run = start(client).json()
    app.state.storage.append_step("team", run["run_id"], {"action_type": "model_call", "latency_ms": 1500})
    row = {
        "run_id": run["run_id"],
        "workflow": "ResearchWorkflow",
        "project": "default",
        "status": "failed",
        "budget": {"tokens": 100, "token_limit": 100, "cost_usd": 5, "cost_limit_usd": 5},
    }
    send = AsyncMock()
    monkeypatch.setattr("app.workflow_notifications.send_notification", send)
    asyncio.run(notify_run(internal(app), row))
    asyncio.run(notify_run(internal(app), row))
    assert send.await_count == 1
    assert send.call_args.args[1] == "webhook"
    assert send.call_args.args[2]["event"] == "slow_step"
    client.put(
        "/v1/team/alert-rules",
        headers={**auth("admin"), "If-Match": "1"},
        json={**rules, "events": ["failed"], "channels": []},
    )
    asyncio.run(notify_run(internal(app), row))
    assert send.await_count == 1


def test_failed_run_exposes_type_and_remediation_without_exception_content(team_gateway):
    client, app = team_gateway
    run = start(client).json()
    execution = app.state.temporal_client.executions[run["workflow_id"]]
    execution.status = "FAILED"
    execution.result = AsyncMock(
        side_effect=WorkflowFailureError(cause=ApplicationError("private provider key", type="ProviderDenied"))
    )
    response = client.get(f"/v1/workflow-runs/{run['run_id']}", headers=auth("viewer"))
    assert response.status_code == 200, response.text
    assert response.json()["error"]["code"] == "ProviderDenied"
    assert "private provider key" not in response.text and "private workflow input" not in response.text
