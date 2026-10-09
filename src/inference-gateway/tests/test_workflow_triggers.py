import asyncio
import hashlib
import hmac
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.policy import SandboxPolicySet, TeamNotifications, WorkflowTrigger
from app.workflow_notifications import CLAIM, RELEASE, notify_run
from app.workflow_triggers import reconcile_schedules
from fastapi import Request
from temporalio.client import ScheduleAlreadyRunningError, ScheduleOverlapPolicy

from tests.test_teams import TeamRedis, auth, team_gateway  # noqa: F401

SECRET = "test-team-webhook-secret-at-least-32-characters"
PATH = "/v1/hooks/team/ResearchWorkflow/inbound"


class TriggerRedis(TeamRedis):
    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()

    def eval(self, script, count, key, *args):
        with self.lock:
            if script == CLAIM:
                return self.setnx(key, args[0])
            if script == RELEASE:
                return self.data.pop(key, None) if self.data.get(key) == args[0] else 0
            return super().eval(script, count, key, *args)


class ScheduleHandle:
    def __init__(self, schedule):
        self.schedule = schedule

    async def describe(self, **kwargs):
        return SimpleNamespace(schedule=self.schedule, info=SimpleNamespace(next_action_times=[datetime.now(UTC)]))

    async def update(self, updater, **kwargs):
        self.schedule = updater(None).schedule

    async def pause(self, **kwargs):
        self.schedule.state.paused = True

    async def unpause(self, **kwargs):
        self.schedule.state.paused = False

    async def delete(self, **kwargs):
        self.deleted = True


@pytest.fixture
def gateway(team_gateway, monkeypatch):  # noqa: F811
    client, app = team_gateway
    app.state.budget_tracker.client = TriggerRedis()
    team = app.state.sandbox_policy_set.policies["team"]
    policy = team.workflows["ResearchWorkflow"].model_copy(
        update={
            "triggers": {
                "inbound": WorkflowTrigger(kind="webhook", project="default"),
                "daily": WorkflowTrigger(kind="cron", project="default", cron="0 9 * * *", input={"topic": "report"}),
            }
        }
    )
    app.state.sandbox_policy_set.policies["team"] = replace(
        team, workflows={"ResearchWorkflow": policy}, webhook_secret_env="TEAM_HOOK_SECRET"
    )
    monkeypatch.setenv("TEAM_HOOK_SECRET", SECRET)
    schedules = {}

    async def create_schedule(id, schedule, **kwargs):
        if id in schedules:
            raise ScheduleAlreadyRunningError()
        schedules[id] = ScheduleHandle(schedule)

    app.state.temporal_client.create_schedule = create_schedule
    app.state.temporal_client.get_schedule_handle = schedules.__getitem__
    return client, app, schedules


def signed(body=b'{"topic":"from webhook"}', *, path=PATH, timestamp=None, delivery="event-1", secret=SECRET):
    stamp = str(int(time()) if timestamp is None else timestamp)
    signature = hmac.new(secret.encode(), f"{stamp}.{delivery}.{path}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "X-AW-Timestamp": stamp,
        "X-AW-Delivery": delivery,
        "X-AW-Signature": "sha256=" + signature,
        "Content-Type": "application/json",
    }


@pytest.mark.parametrize("streamed", [False, True])
def test_webhook_body_is_bounded_before_authentication(gateway, monkeypatch, streamed):
    client, app, _ = gateway
    binding = AsyncMock(return_value=False)
    monkeypatch.setattr("app.workflow_triggers.bind_webhook", binding)
    body = b"x" * (app.state.settings.max_request_body_bytes + 1)
    response = client.post(PATH, content=iter([body]) if streamed else body)
    assert response.status_code == 413
    assert response.json()["detail"]["reason"] == "request_body_too_large"
    binding.assert_not_awaited()


def internal(app):
    return Request(
        {
            "type": "http",
            "app": app,
            "headers": [],
            "state": {
                "sandbox_id": "team",
                "sandbox_bound": True,
                "principal": {"role": "admin"},
            },
        }
    )


@pytest.mark.parametrize("change", ["missing", "old", "future", "body", "path", "secret", "team"])
def test_webhook_authentication_fails_closed(gateway, change):
    client, app, _ = gateway
    body = b'{"topic":"from webhook"}'
    headers = signed(body)
    if change == "missing":
        headers = auth("builder")
    elif change in {"old", "future"}:
        headers = signed(body, timestamp=int(time()) + (-600 if change == "old" else 600))
    elif change == "body":
        body = b'{"topic":"altered"}'
    elif change == "path":
        headers = signed(body, path=PATH.replace("inbound", "another"))
    elif change == "secret":
        headers = signed(body, secret="another-team-secret")
    else:
        headers["X-Sandbox-ID"] = "other"
    response = client.post(PATH, content=body, headers=headers)
    assert response.status_code == (403 if change == "team" else 401)
    assert app.state.temporal_client.executions == {}


def test_signed_payload_identity_receipt_and_replay(gateway, caplog):
    client, app, _ = gateway
    body = b'{"topic":"private payload"}'
    with caplog.at_level(logging.INFO, logger="gateway.audit"):
        response = client.post(PATH, content=body, headers=signed(body))
        assert response.status_code == 201, response.text
        assert client.post(PATH, content=body, headers=signed(body)).status_code == 409
    assert len(app.state.temporal_client.executions) == 1
    run_id = response.json()["run_id"]
    run = client.get(f"/v1/workflow-runs/{run_id}", headers=auth("viewer")).json()
    assert run["submitted_by"]["auth"] == "webhook"
    assert run["timeline"][0]["receipt"]["kind"] == "webhook"
    store = app.state.budget_tracker.client.data
    metadata = next(json.loads(v) for k, v in store.items() if k.endswith(":metadata"))
    assert metadata["input"] == {"topic": "private payload"}
    assert "private payload" not in caplog.text and SECRET not in caplog.text
    assert client.get(f"/v1/workflow-runs/{run_id}", headers=auth("other")).status_code == 404


def test_concurrent_deliveries_start_once(gateway):
    client, app, _ = gateway
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: client.post(PATH, content=b'{"topic":"from webhook"}', headers=signed()), range(2))
        )
    assert all(result.status_code in {201, 409} for result in results)
    assert len(app.state.temporal_client.executions) == 1


def test_trigger_history_provenance_paging_retention_and_scope(gateway):
    client, app, _ = gateway
    url = "/v1/workflow-triggers/ResearchWorkflow/daily/fire"
    firing = {"firing_id": str(uuid4())}
    scheduled = client.post(url, headers=auth("worker"), json=firing).json()
    assert client.post(url, headers=auth("worker"), json=firing).json() == scheduled
    webhook = client.post(PATH, content=b'{"topic":"from webhook"}', headers=signed()).json()
    manual = client.post("/v1/workflow-runs", headers=auth("builder"), json={
        "workflow": "ResearchWorkflow", "input": {"topic": "manual"},
    }).json()
    params = {"workflow": "ResearchWorkflow", "trigger": "daily", "limit": 1}
    page = client.get("/v1/workflow-runs", params=params, headers=auth("viewer")).json()
    assert not page["runs"] and page["next_cursor"]
    assert client.get("/v1/workflow-runs", params={**params, "trigger": "inbound", "cursor": page["next_cursor"]},
                      headers=auth("viewer")).status_code == 422
    for expected in ([], [scheduled["run_id"]]):
        page = client.get("/v1/workflow-runs", params={**params, "cursor": page["next_cursor"]},
                          headers=auth("viewer")).json()
        assert [row["run_id"] for row in page["runs"]] == expected
    assert page["next_cursor"] is None
    assert page["runs"][0]["trigger"] == {"name": "daily", "kind": "cron"}
    detail = client.get(f"/v1/workflow-runs/{webhook['run_id']}", headers=auth("viewer")).json()
    assert detail["trigger"] == {"name": "inbound", "kind": "webhook"}
    assert any(step["action"] == "trigger" for step in detail["timeline"])
    assert "trigger" not in client.get(f"/v1/workflow-runs/{manual['run_id']}", headers=auth("viewer")).json()
    assert client.get("/v1/workflow-runs", params=params, headers=auth("other")).json()["runs"] == []
    assert client.get("/v1/workflow-runs", params={**params, "project": "default"},
                      headers=auth("project")).status_code == 404
    assert client.get("/v1/workflow-runs?trigger=daily", headers=auth("viewer")).status_code == 422
    assert client.post("/v1/workflow-runs", headers=auth("builder"), json={
        "input": {"topic": "spoof"}, "trigger": {"name": "daily", "kind": "cron"},
    }).status_code == 422
    app.state.temporal_client.executions[scheduled["workflow_id"]].status = "COMPLETED"
    detail = client.get(f"/v1/workflow-runs/{scheduled['run_id']}", headers=auth("viewer")).json()
    assert detail["trigger"] == {"name": "daily", "kind": "cron"}
    # Removed schedules remain discoverable until their run records expire.
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(team, workflows={})
    assert client.get("/v1/workflow-runs", params={**params, "limit": 100},
                      headers=auth("viewer")).json()["runs"][0]["trigger"] == detail["trigger"]
    prefix = app.state.settings.sandbox_budget_key_prefix
    app.state.budget_tracker.client.delete(f"{prefix}:workflow:team:{scheduled['run_id']}:metadata")
    assert client.get("/v1/workflow-runs", params={**params, "limit": 100},
                      headers=auth("viewer")).json()["runs"] == []


def test_native_github_signature_cannot_replay_with_changed_delivery_or_route(gateway):
    client, app, _ = gateway
    body = b'{"topic":"native GitHub payload"}'
    headers = {
        "X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
        "X-GitHub-Delivery": str(uuid4()),
        "Content-Type": "application/json",
    }
    assert client.post(PATH, content=body + b" ", headers=headers).status_code == 401
    assert client.post(PATH, content=body, headers=headers).status_code == 201
    headers["X-GitHub-Delivery"] = str(uuid4())
    assert client.post(PATH, content=body, headers=headers).status_code == 409
    team = app.state.sandbox_policy_set.policies["team"]
    policy = team.workflows["ResearchWorkflow"]
    updated = policy.model_copy(
        update={"triggers": {**policy.triggers, "another": WorkflowTrigger(kind="webhook", project="private")}}
    )
    app.state.sandbox_policy_set.policies["team"] = replace(team, workflows={"ResearchWorkflow": updated})
    assert client.post(PATH.replace("inbound", "another"), content=body, headers=headers).status_code == 409
    assert len(app.state.temporal_client.executions) == 1


def test_ambiguous_start_can_retry_without_duplicate(gateway, monkeypatch):
    from temporalio.service import RPCError, RPCStatusCode

    client, app, _ = gateway
    original = app.state.temporal_client.start_workflow

    async def lost_response(*args, **kwargs):
        await original(*args, **kwargs)
        raise RPCError("lost", RPCStatusCode.UNAVAILABLE, b"")

    monkeypatch.setattr(app.state.temporal_client, "start_workflow", lost_response)
    assert client.post(PATH, content=b'{"topic":"from webhook"}', headers=signed()).status_code == 503
    monkeypatch.setattr(app.state.temporal_client, "start_workflow", original)
    assert client.post(PATH, content=b'{"topic":"from webhook"}', headers=signed()).status_code == 201
    assert len(app.state.temporal_client.executions) == 1


def test_trigger_roles_project_scope_and_pause(gateway):
    client, _, _ = gateway
    url = "/v1/workflow-triggers/ResearchWorkflow/inbound"
    assert client.patch(url, headers=auth("viewer"), json={"paused": True}).status_code == 403
    assert client.patch(url, headers=auth("project"), json={"paused": True}).status_code == 404
    assert client.get("/v1/workflow-triggers", headers=auth("project")).json()["triggers"] == []
    assert client.patch(url, headers=auth("builder"), json={"paused": True}).status_code == 200
    assert client.post(PATH, content=b'{"topic":"from webhook"}', headers=signed()).status_code == 409
    assert client.patch(url, headers=auth("builder"), json={"paused": False}).status_code == 200
    assert client.post(PATH, content=b'{"topic":"from webhook"}', headers=signed()).status_code == 201


def test_temporal_schedule_reconciliation_and_pause_survives_restart(gateway):
    client, app, schedules = gateway
    asyncio.run(reconcile_schedules(internal(app)))
    schedule = next(iter(schedules.values()))
    assert schedule.schedule.spec.cron_expressions == ["0 9 * * *"]
    assert schedule.schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
    assert schedule.schedule.action.workflow == "AgentWorkflowsTrigger"
    url = "/v1/workflow-triggers/ResearchWorkflow/daily"
    assert client.patch(url, headers=auth("builder"), json={"paused": True}).status_code == 200
    asyncio.run(reconcile_schedules(internal(app)))
    assert schedule.schedule.state.paused
    row = client.get("/v1/workflow-triggers", headers=auth("viewer")).json()["triggers"][1]
    assert row["paused"] and row["next_fire_at"]
    assert client.patch(url, headers=auth("builder"), json={"paused": False}).status_code == 200
    assert not schedule.schedule.state.paused
    body = {"firing_id": str(uuid4())}
    assert client.post(url + "/fire", headers=auth("builder"), json=body).status_code == 403
    first = client.post(url + "/fire", headers=auth("worker"), json=body)
    second = client.post(url + "/fire", headers=auth("worker"), json=body)
    assert first.status_code == 200 and first.json() == second.json()
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(team, workflows={})
    asyncio.run(reconcile_schedules(internal(app)))
    assert schedule.deleted


def test_config_rejects_invalid_trigger_and_smtp(tmp_path):
    path = tmp_path / "policy.json"
    base = {
        "apiVersion": "platform.ai/v1alpha1",
        "kind": "SandboxPolicySet",
        "spec": {
            "policies": [
                {
                    "sandboxId": "team",
                    "projects": ["default"],
                    "workflows": {
                        "Flow": {
                            "allowedProviders": [],
                            "allowedModels": [],
                            "triggers": {"hook": {"kind": "webhook", "project": "default"}},
                        }
                    },
                }
            ]
        },
    }
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match="webhookSecretEnv"):
        SandboxPolicySet.from_path(path)
    base["spec"]["policies"][0]["webhookSecretEnv"] = "SECRET_ENV"
    base["spec"]["policies"][0]["workflows"]["Flow"]["triggers"]["hook"]["project"] = "another"
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match="team project"):
        SandboxPolicySet.from_path(path)
    with pytest.raises(ValueError, match="cron expression"):
        WorkflowTrigger(kind="cron", project="default")
    with pytest.raises(ValueError):
        TeamNotifications.model_validate(
            {
                "consoleUrl": "https://console/",
                "smtp": {
                    "host": "mail",
                    "sender": "aw@example.test",
                    "recipients": ["user@example.test\nInjected"],
                },
            }
        )


def test_notification_dedup_retry_failure_budget_and_event_persistence(gateway, monkeypatch):
    client, app, _ = gateway
    team = app.state.sandbox_policy_set.policies["team"]
    config = TeamNotifications.model_validate(
        {
            "consoleUrl": "https://console.test/console/",
            "slackWebhookEnv": "SLACK",
            "webhookEnv": "HOOK",
            "smtp": {"host": "smtp", "sender": "aw@example.test", "recipients": ["team@example.test"]},
        }
    )
    app.state.sandbox_policy_set.policies["team"] = replace(team, notifications=config)
    run_id = client.post(PATH, content=b'{"topic":"from webhook"}', headers=signed()).json()["run_id"]
    row = client.get(f"/v1/workflow-runs/{run_id}", headers=auth("viewer")).json()
    delivered = []

    async def send(config, channel, payload):
        if channel == "slack" and not delivered:
            raise OSError("secret-token-in-error")
        delivered.append((channel, payload))

    monkeypatch.setattr("app.workflow_notifications.send_notification", send)
    now = time()
    monkeypatch.setattr("app.workflow_notifications.time", lambda: now)
    asyncio.run(notify_run(internal(app), row))
    assert {channel for channel, _ in delivered} == {"webhook", "email"}
    row.update(status="completed", progress={})
    now += 31
    asyncio.run(notify_run(internal(app), row))
    asyncio.run(notify_run(internal(app), row))
    assert len(delivered) == 3
    row["status"] = "failed"
    row["budget"] = {"tokens": 80, "token_limit": 100, "cost_usd": 0, "cost_limit_usd": 5}
    asyncio.run(notify_run(internal(app), row))
    assert len(delivered) == 9
    assert {payload["event"] for _, payload in delivered} == {"awaiting_approval", "failed", "budget_threshold"}
    assert all(payload["console_url"] == f"https://console.test/console#run/{run_id}" for _, payload in delivered)
    run = client.get(f"/v1/workflow-runs/{run_id}", headers=auth("viewer")).json()
    receipts = [step["receipt"] for step in run["timeline"] if step["action"] == "notification"]
    assert {receipt["outcome"] for receipt in receipts} == {"attempted", "retrying", "delivered"}
    assert "secret-token" not in json.dumps(receipts)
    event_url = f"/v1/workflow-runs/{run_id}/approval-waiting"
    assert client.post(event_url, headers=auth("builder")).status_code == 403
    assert client.post(event_url, headers=auth("worker")).status_code == 200


def test_budget_crossing_survives_settlement_before_monitor_poll(gateway):
    from app.policy import ModelRoute
    from app.workflow_budget import reserve_run, run_key, settle_run_model

    client, app, _ = gateway
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(
        team,
        notifications=TeamNotifications.model_validate(
            {
                "consoleUrl": "https://console.test/console/",
                "webhookEnv": "HOOK",
            }
        ),
    )
    run = client.post(PATH, content=b'{"topic":"from webhook"}', headers=signed()).json()
    assert (
        client.put(
            f"/v1/workflow-runs/{run['run_id']}",
            headers={**auth("worker"), "X-Workflow-ID": run["workflow_id"]},
            json={"workflow": "ResearchWorkflow", "token_limit": 100, "cost_limit_usd": 1},
        ).status_code
        == 200
    )
    request = internal(app)
    request.state.workflow_run_id = run["run_id"]
    reservation = asyncio.run(reserve_run(request, 80, 0.8))
    asyncio.run(
        settle_run_model(
            request,
            reservation,
            ModelRoute("primary", "openai", input_usd_per_1k_tokens=1, output_usd_per_1k_tokens=1),
            {"usage": {"total_tokens": 7}},
        )
    )
    store = app.state.budget_tracker.client.data
    assert store[run_key(request)]["tokens"] == 7
    assert store[run_key(request) + ":notifications"] == {"budget_threshold": "1"}
