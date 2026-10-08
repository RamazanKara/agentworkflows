# ruff: noqa: F811  (shared gateway fixtures are imported, then requested by name)
import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.state_migrations import migrate_run_retention
from app.workflow_content import content_key
from app.workflow_retention import TERMINAL_STATES

from tests.test_teams import auth, start, team_gateway  # noqa: F401


@pytest.mark.parametrize("status", sorted(TERMINAL_STATES))
def test_terminal_runs_expire_at_close_time_without_extending_content(team_gateway, status):
    client, app = team_gateway
    settings = app.state.settings
    run = start(client).json()
    path = f"/v1/workflow-runs/{run['run_id']}"
    base = f"{settings.sandbox_budget_key_prefix}:workflow:team:{run['run_id']}"
    store = app.state.budget_tracker.client
    worker = {**auth("worker"), "X-Workflow-ID": run["workflow_id"]}
    assert client.put(path, headers=worker, json={"workflow": "ResearchWorkflow"}).status_code == 200
    store.rpush(base + ":timeline", json.dumps({"ts": 1, "record_hash": "receipt", "chain_id": "chain"}))
    store.hset(base + ":capture", "model", "redacted")
    store.setex(content_key(base, "model"), 100, "{}")
    store.hset(base + ":notifications", "failed", "1")
    store.set(base + ":notification:failed:email", "{}")
    assert client.get(path, headers=auth("viewer")).status_code == 200
    assert store.ttl(base + ":metadata") == -1
    execution = app.state.temporal_client.executions[run["workflow_id"]]
    execution.status = status.upper()
    execution.close_time = datetime.now(UTC) - timedelta(seconds=60)
    deadline = int(execution.close_time.timestamp() + settings.run_record_retention_seconds)
    assert client.get(path, headers=auth("viewer")).status_code == 200
    for key in (
        base, base + ":metadata", base + ":timeline", base + ":capture", base + ":notifications",
        base + ":notification:failed:email", f"{settings.sandbox_budget_key_prefix}:start:{run['workflow_id']}",
    ):
        assert store.expires[key] == deadline
    assert store.ttl(content_key(base, "model")) == 100
    assert client.get(path, headers=auth("viewer")).status_code == 200
    assert store.expires[base + ":metadata"] == deadline
    # A duplicate worker initialization must not remove the metadata TTL.
    assert client.put(path, headers=worker, json={"workflow": "ResearchWorkflow"}).status_code == 200
    assert store.expires[base + ":metadata"] == deadline
    store.now = deadline + 1
    assert client.get(path, headers=auth("viewer")).status_code == 404
    assert client.get("/v1/workflow-runs", headers=auth("viewer")).json() == {
        "runs": [], "next_offset": None, "next_cursor": None,
    }
    assert not store.data[f"{settings.sandbox_budget_key_prefix}:runs:team:default"]


def test_online_migration_backfills_legacy_terminal_runs_and_is_idempotent(team_gateway):
    client, app = team_gateway
    settings = app.state.settings
    object.__setattr__(settings, "run_record_retention_seconds", 120)
    active = start(client).json()
    recent = start(client).json()
    old = start(client).json()
    for run, seconds in ((recent, 30), (old, 300)):
        execution = app.state.temporal_client.executions[run["workflow_id"]]
        execution.status = "FAILED"
        execution.close_time = datetime.now(UTC) - timedelta(seconds=seconds)
    store = app.state.budget_tracker.client
    prefix = settings.sandbox_budget_key_prefix

    def key(run):
        return f"{prefix}:workflow:team:{run['run_id']}:metadata"

    assert asyncio.run(migrate_run_retention(app))
    assert store.ttl(key(active)) == -1
    assert 88 <= store.ttl(key(recent)) <= 91
    assert store.get(key(old)) is None
    deadline = store.expires[key(recent)]
    assert asyncio.run(migrate_run_retention(app))
    assert store.expires[key(recent)] == deadline


def test_expired_records_do_not_break_filtered_pagination(team_gateway):
    client, app = team_gateway
    first, second, third = [start(client).json() for _ in range(3)]
    store = app.state.budget_tracker.client
    prefix = app.state.settings.sandbox_budget_key_prefix
    store.delete(f"{prefix}:workflow:team:{third['run_id']}:metadata")
    page = client.get("/v1/workflow-runs?workflow=ResearchWorkflow&limit=1", headers=auth("viewer")).json()
    assert page["runs"] == [] and page["next_offset"] == 0 and page["next_cursor"]
    page = client.get("/v1/workflow-runs?limit=1&offset=0", headers=auth("viewer")).json()
    assert page["runs"][0]["run_id"] == second["run_id"]
    page = client.get("/v1/workflow-runs?limit=1&offset=1", headers=auth("viewer")).json()
    assert page["runs"][0]["run_id"] == first["run_id"] and page["next_offset"] is None
