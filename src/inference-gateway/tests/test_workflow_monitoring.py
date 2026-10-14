# ruff: noqa: F811  (shared gateway fixtures are imported, then requested by name)
import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import time

import pytest
from app.metrics import WORKFLOW_RUNS
from app.policy import TeamNotifications
from app.workflow_notifications import CLAIM, notify_run
from app.workflow_operations import monitor_workflows, refresh_workflow_metrics

from tests.test_teams import auth, start, team_gateway  # noqa: F401


def count_temporal(app, monkeypatch):
    calls = []
    for execution in app.state.temporal_client.executions.values():
        for name in ("describe", "query", "result"):
            original = getattr(execution, name)

            async def call(*args, _original=original, _name=name, **kwargs):
                calls.append(_name)
                return await _original(*args, **kwargs)

            monkeypatch.setattr(execution, name, call)
    return calls


def gauge(team, status):
    return WORKFLOW_RUNS.labels(team, status)._value.get()


def test_finished_run_is_described_once_and_kept_until_retention(team_gateway, monkeypatch):
    client, app = team_gateway
    run = start(client).json()
    execution = app.state.temporal_client.executions[run["workflow_id"]]
    execution.status, execution.stage = "COMPLETED", "published"
    execution.close_time = datetime.now(UTC) - timedelta(seconds=60)
    calls = count_temporal(app, monkeypatch)
    path = f"/v1/workflow-runs/{run['run_id']}"
    first = client.get(path, headers=auth("viewer"))
    assert first.status_code == 200 and first.json()["outcome"] == "published"
    assert calls == ["describe", "result"]
    listed = client.get("/v1/workflow-runs", headers=auth("viewer")).json()["runs"]
    second = client.get(path, headers=auth("viewer")).json()
    assert calls == ["describe", "result"]
    assert listed[0]["status"] == "completed" and "result" not in listed[0]
    assert second["result"] == first.json()["result"] and second["budget"] == first.json()["budget"]
    store = app.state.budget_tracker.client
    base = f"{app.state.settings.sandbox_budget_key_prefix}:workflow:team:{run['run_id']}"
    deadline = execution.close_time.timestamp() + app.state.settings.run_record_retention_seconds
    assert abs(store.expires[base + ":snapshot"] - deadline) < 5
    store.now = deadline + 1
    assert client.get(path, headers=auth("viewer")).status_code == 404


def test_running_runs_are_never_served_from_a_snapshot(team_gateway, monkeypatch):
    client, app = team_gateway
    run = start(client).json()
    calls = count_temporal(app, monkeypatch)
    path = f"/v1/workflow-runs/{run['run_id']}"
    assert client.get(path, headers=auth("viewer")).json()["status"] == "running"
    app.state.temporal_client.executions[run["workflow_id"]].status = "CANCELED"
    assert client.get(path, headers=auth("viewer")).json()["status"] == "canceled"
    assert calls.count("describe") == 2


def test_timeline_content_is_read_in_one_batch(team_gateway, monkeypatch):
    from app.workflow_content import content_key

    client, app = team_gateway
    run = start(client).json()
    store = app.state.budget_tracker.client
    base = f"{app.state.settings.sandbox_budget_key_prefix}:workflow:team:{run['run_id']}"
    for index in range(12):
        store.rpush(base + ":timeline", json.dumps({
            "action_type": "model_call", "workflow_step_id": f"step-{index}", "ts": time() + index,
            "record_hash": f"receipt-{index}", "chain_id": "chain",
        }))
    store.setex(content_key(base, "step-0"), 60, json.dumps({"input": "kept", "output": None}))
    store.hset(base + ":capture", "step-1", "redacted")
    reads = []
    nested = []
    for name in ("get", "mget", "hget", "hgetall"):
        original = getattr(store, name)

        def counted(*args, _original=original, _name=name):
            # The fake's mget and hgetall call its own get; count only the gateway's reads.
            if not nested and (":content:" in str(args[0]) or str(args[0]).endswith(":capture")):
                reads.append(_name)
            nested.append(_name)
            try:
                return _original(*args)
            finally:
                nested.pop()

        monkeypatch.setattr(store, name, counted)
    timeline = client.get(f"/v1/workflow-runs/{run['run_id']}", headers=auth("viewer")).json()["timeline"]
    assert reads == ["mget", "hgetall"]
    assert timeline[0]["content"] == {"input": "kept", "output": None}
    assert timeline[1]["content_reason"] == "expired" and timeline[2]["content_reason"] == "capture_off"


def test_run_listing_describes_concurrently_and_keeps_order(team_gateway, monkeypatch):
    client, app = team_gateway
    runs = [start(client).json() for _ in range(12)]
    active = peak = 0

    for execution in app.state.temporal_client.executions.values():
        original = execution.describe

        async def slow(*args, _original=original, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return await _original(*args, **kwargs)

        monkeypatch.setattr(execution, "describe", slow)
    page = client.get("/v1/workflow-runs?limit=12", headers=auth("viewer")).json()
    assert [row["run_id"] for row in page["runs"]] == [run["run_id"] for run in reversed(runs)]
    assert 1 < peak <= 8


def test_refresh_counts_runs_and_reads_finished_runs_from_snapshots(team_gateway, monkeypatch):
    client, app = team_gateway
    runs = [start(client).json() for _ in range(5)]
    statuses = ["COMPLETED", "FAILED", "CANCELED", "RUNNING", "RUNNING"]
    for run, status in zip(runs, statuses, strict=True):
        app.state.temporal_client.executions[run["workflow_id"]].status = status
    app.state.temporal_client.executions[runs[4]["workflow_id"]].stage = "draft"
    calls = count_temporal(app, monkeypatch)
    asyncio.run(refresh_workflow_metrics(app))
    expected = {"completed": 1, "failed": 1, "canceled": 1, "awaiting_approval": 1, "running": 1, "timed_out": 0}
    assert {status: gauge("team", status) for status in expected} == expected
    first = len(calls)
    asyncio.run(refresh_workflow_metrics(app))
    # Only the two live runs reach Temporal again: a describe and a status query each.
    assert len(calls) - first == 4


def test_monitor_survives_unexpected_errors(team_gateway, monkeypatch, caplog):
    _, app = team_gateway
    passes = []

    async def refresh(app):
        passes.append(1)
        if len(passes) == 1:
            raise KeyError("corrupt record")

    class Stop(Exception):
        pass

    async def sleep(seconds):
        assert seconds == 30
        if len(passes) == 2:
            raise Stop

    monkeypatch.setattr("app.workflow_operations.refresh_workflow_metrics", refresh)
    monkeypatch.setattr("app.workflow_operations.asyncio.sleep", sleep)
    caplog.set_level(logging.ERROR)
    with pytest.raises(Stop):
        asyncio.run(monitor_workflows(app))
    assert len(passes) == 2
    assert "Workflow metrics refresh failed" in caplog.text


def notifications(app):
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(
        team,
        notifications=TeamNotifications.model_validate(
            {"consoleUrl": "https://console.test/", "webhookEnv": "HOOK"}
        ),
    )


def internal(app):
    from starlette.requests import Request

    return Request({
        "type": "http", "path": "/v1/internal", "app": app, "headers": [],
        "state": {"sandbox_id": "team", "sandbox_bound": True, "principal": {"role": "admin"}},
    })


def test_settled_notifications_skip_the_delivery_lock(team_gateway, monkeypatch):
    client, app = team_gateway
    notifications(app)
    run = start(client).json()
    app.state.temporal_client.executions[run["workflow_id"]].status = "FAILED"
    row = client.get(f"/v1/workflow-runs/{run['run_id']}", headers=auth("viewer")).json()
    sent = []

    async def send(config, channel, payload):
        sent.append((channel, payload["event"]))

    monkeypatch.setattr("app.workflow_notifications.send_notification", send)
    asyncio.run(notify_run(internal(app), row))
    assert sent == [("webhook", "failed")]
    store = app.state.budget_tracker.client
    original = store.eval
    locks = []

    def counting(script, *args):
        if script == CLAIM:
            locks.append(args)
        return original(script, *args)

    monkeypatch.setattr(store, "eval", counting)
    asyncio.run(notify_run(internal(app), row))
    assert sent == [("webhook", "failed")] and locks == []


def test_finished_run_receipts_are_scanned_for_slow_steps_once(team_gateway, monkeypatch):
    client, app = team_gateway
    notifications(app)
    store = app.state.budget_tracker.client
    rules = {"events": ["slow_step"], "channels": ["webhook"], "budget_threshold": 0.8, "slow_step_ms": 1000}
    assert client.put(
        "/v1/team/alert-rules", headers={**auth("admin"), "If-Match": "0"}, json=rules
    ).status_code == 200
    run = start(client).json()
    base = f"{app.state.settings.sandbox_budget_key_prefix}:workflow:team:{run['run_id']}"
    store.rpush(base + ":timeline", json.dumps({
        "action_type": "model_call", "latency_ms": 10, "ts": time(), "record_hash": "receipt", "chain_id": "chain",
    }))
    reads = []
    original = app.state.storage.run_steps

    def run_steps(team, run_id):
        reads.append(run_id)
        return original(team, run_id)

    monkeypatch.setattr(app.state.storage, "run_steps", run_steps)
    monkeypatch.setattr("app.workflow_notifications.send_notification", lambda *args: asyncio.sleep(0))
    row = client.get(f"/v1/workflow-runs/{run['run_id']}", headers=auth("viewer")).json()
    reads.clear()
    asyncio.run(notify_run(internal(app), row))
    asyncio.run(notify_run(internal(app), row))
    assert len(reads) == 2
    row["status"] = "completed"
    asyncio.run(notify_run(internal(app), row))
    asyncio.run(notify_run(internal(app), row))
    assert len(reads) == 3
    assert store.hgetall(base + ":notifications")["slow_step"] == "0"
