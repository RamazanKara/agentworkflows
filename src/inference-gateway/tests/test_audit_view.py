# ruff: noqa: F811
import json
import logging
from dataclasses import replace
from time import time

import pytest
from app.audit import AUDIT_GENESIS, chain_audit_event, emit_audit_record
from app.audit_view import APPEND
from app.settings import Settings
from fastapi import Request
from redis.exceptions import ConnectionError

from tests.test_audit_verify import _double_logged_lines, _load_verifier
from tests.test_teams import auth, team_gateway  # noqa: F401

PATH = "/v1/team/audit"


def test_retention_configuration(monkeypatch):
    monkeypatch.delenv("AUDIT_VIEW_RETENTION_SECONDS", raising=False)
    assert Settings.from_env().audit_view_retention_seconds == 90 * 86400
    monkeypatch.setenv("AUDIT_VIEW_RETENTION_SECONDS", "60")
    settings = Settings.from_env()
    assert settings.audit_view_retention_seconds == 60
    with pytest.raises(ValueError, match="audit_view_retention_seconds"):
        replace(settings, audit_view_retention_seconds=0)


def record(app, **fields):
    event = {
        "event": "inference_request", "chain_id": app.state.audit_chain_id,
        "sandbox_id": "team", "principal": {"key_id": "admin"}, "project": "default", "ts": time(),
        **fields,
    }
    request = Request({"type": "http", "app": app})
    request.state.sandbox_id = event["sandbox_id"]
    chain_audit_event(request, event)
    emit_audit_record(event)
    return event


def rows(app, team="team"):
    key = f"{app.state.settings.sandbox_budget_key_prefix}:audit:{team}"
    return app.state.budget_tracker.client.streams[key]


def verify(client, **params):
    response = client.get(PATH + "/verify", headers=auth("admin"), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_stream_copies_logged_event_and_keeps_original_verifier_working(team_gateway, caplog):
    client, app = team_gateway
    caplog.set_level(logging.INFO)
    response = client.post("/v1/chat/completions", headers=auth("builder"), json={
        "model": "primary", "max_tokens": 20, "messages": [{"role": "user", "content": "private prompt text"}],
    })
    assert response.status_code == 200, response.text
    verifier = _load_verifier()
    logged = verifier.deduplicate(verifier.extract_audit_events(_double_logged_lines(caplog)))
    page = client.get(PATH, headers=auth("admin"))
    assert page.headers["cache-control"] == "no-store"
    entry = page.json()["events"][0]
    assert entry["event"] == logged[0]
    assert "private prompt text" not in json.dumps(entry)
    assert entry["record_hash"] == logged[0]["record_hash"]
    assert entry["sequence"] == app.state.audit_chain_count and entry["team_sequence"] == 1
    assert all(verifier.verify_chain(chain).ok for chain in verifier.group_into_chains(logged))
    assert verify(client)["ok"] is True


def test_retention_time_length_and_idle_read_trimming(team_gateway, monkeypatch):
    client, app = team_gateway
    store = app.state.budget_tracker.client
    monkeypatch.setattr("app.audit_view.MAX_EVENTS", 2)
    app.state.settings = replace(app.state.settings, audit_view_retention_seconds=60)
    for _ in range(3):
        record(app)
    assert len(rows(app)) == 2
    assert next(iter(store.stream_expires.values())) == 60
    result = verify(client)
    assert result["ok"] and result["checked"] == 2
    assert result["boundaries"][0]["reason"] == "retained_range_start"
    store.now += 61
    record(app)
    assert len(rows(app)) == 1
    assert verify(client)["ok"]
    monkeypatch.setattr("app.audit_view.time", lambda: store.now + 61)
    assert client.get(PATH, headers=auth("admin")).json()["events"] == []


@pytest.mark.parametrize("params", [
    {"event_type": "team_settings_changed"}, {"actor": "alice"}, {"project": "private"},
    {"run_id": "run-1"}, {"from": 101, "to": 102},
    {"event_type": "team_settings_changed", "actor": "alice", "project": "private", "run_id": "run-1"},
])
def test_filters(team_gateway, params):
    client, app = team_gateway
    record(app, ts=100)
    wanted = record(
        app, ts=102, event="team_settings_changed", actor="alice", project="private", workflow_run_id="run-1",
    )
    record(app, ts=103)
    data = client.get(PATH, headers=auth("admin"), params=params).json()
    assert [entry["event"] for entry in data["events"]] == [wanted]


def test_actor_subject_and_team_isolation(team_gateway):
    client, app = team_gateway
    wanted = record(app, principal={"sub": "alice", "key_id": "key"})
    record(app, sandbox_id="other", actor="alice")
    data = client.get(PATH, headers=auth("admin"), params={"actor": "alice"}).json()
    assert [entry["event"] for entry in data["events"]] == [wanted]


def test_cursor_pages_are_newest_first_without_duplicates_during_appends(team_gateway, monkeypatch):
    client, app = team_gateway
    monkeypatch.setattr("app.audit_view.READ_BATCH", 2)
    events = [record(app, actor="alice" if i % 2 else "bob") for i in range(7)]
    query = {"actor": "alice", "limit": 2}
    page = client.get(PATH, headers=auth("admin"), params=query).json()
    assert [entry["event"] for entry in page["events"]] == [events[5], events[3]]
    record(app, actor="alice")
    page = client.get(PATH, headers=auth("admin"), params={**query, "cursor": page["next_cursor"]}).json()
    assert [entry["event"] for entry in page["events"]] == [events[1]]
    assert page["next_cursor"] is None


@pytest.mark.parametrize("params", [{"limit": 201}, {"limit": 0}, {"cursor": "bad"}, {"from": 2, "to": 1},
                                   {"from": "nan"}, {"to": "inf"}])
def test_invalid_query(team_gateway, params):
    client, _ = team_gateway
    assert client.get(PATH, headers=auth("admin"), params=params).status_code == 422


def test_verification_handles_interleaved_teams_replicas_and_time_range(team_gateway):
    client, app = team_gateway
    record(app, ts=1)
    record(app, sandbox_id="other", ts=2)
    record(app, ts=3)
    app.state.audit_chain_id = "second-process"
    app.state.audit_prev_hash = AUDIT_GENESIS
    app.state.audit_chain_count = 0
    app.state.audit_view_heads = {}
    record(app, ts=4)
    result = verify(client)
    assert result["ok"] and result["checked"] == 3 and result["boundaries"] == []
    result = verify(client, **{"from": 3, "to": 4})
    assert result["ok"] and result["checked"] == 2
    assert result["boundaries"][0]["reason"] == "time_range"


@pytest.mark.parametrize(("change", "reason"), [
    ("payload", "record_hash_mismatch"), ("metadata", "event_metadata_mismatch"),
    ("view", "view_hash_mismatch"), ("delete", "sequence_gap_or_reordered"),
    ("reorder", "sequence_gap_or_reordered"),
    ("malformed", "malformed_event"),
])
def test_verification_reports_first_break(team_gateway, change, reason):
    client, app = team_gateway
    for _ in range(4):
        record(app)
    stored = rows(app)
    if change == "delete":
        del stored[1]
    elif change == "reorder":
        stored[1], stored[2] = (stored[1][0], stored[2][1]), (stored[2][0], stored[1][1])
    else:
        entry = json.loads(stored[1][1]["entry"])
        if change == "payload":
            entry["event"]["actor"] = "forged"
        elif change == "metadata":
            entry["record_hash"] = "0" * 64
        elif change == "malformed":
            entry["event"]["prev_hash"] = None
        else:
            entry["view_hash"] = "0" * 64
        stored[1][1]["entry"] = json.dumps(entry)
    result = verify(client)
    assert result["ok"] is False and result["checked"] == 2
    assert result["first_break"]["reason"] == reason
    assert result["first_break"]["sequence"] == (3 if change in {"delete", "reorder"} else 2)


def test_failed_append_does_not_stop_log_and_leaves_detectable_gap(team_gateway, monkeypatch, caplog):
    client, app = team_gateway
    store = app.state.budget_tracker.client
    original = store.eval
    record(app)

    def fail(script, *args):
        if script == APPEND:
            raise ConnectionError("offline")
        return original(script, *args)

    with monkeypatch.context() as patch:
        patch.setattr(store, "eval", fail)
        caplog.set_level(logging.INFO)
        event = record(app)
    assert json.dumps(event, sort_keys=True) in caplog.text
    record(app)
    assert verify(client)["first_break"]["reason"] == "sequence_gap_or_reordered"


@pytest.mark.parametrize("role", ["builder", "approver", "viewer", "worker", "project", "other"])
@pytest.mark.parametrize("suffix", ["", "/verify"])
def test_admin_role_required_before_query_validation(team_gateway, role, suffix):
    client, _ = team_gateway
    response = client.get(PATH + suffix, headers=auth(role), params={"from": "invalid"})
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "team_role_required"


def test_project_bound_admin_cannot_read_team_wide_audit(team_gateway):
    client, app = team_gateway
    app.state.key_record_set = replace(app.state.key_record_set, records=tuple(
        replace(key, project="private") if key.key_id == "admin" else key for key in app.state.key_record_set.records
    ))
    for suffix in ("", "/verify"):
        response = client.get(PATH + suffix, headers=auth("admin"))
        assert response.status_code == 403
        assert response.json()["detail"]["reason"] == "team_role_required"


def test_disabled_view_is_explicit_and_store_failures_are_503(team_gateway, monkeypatch):
    client, app = team_gateway
    monkeypatch.setattr(app.state.budget_tracker, "backend", "memory")
    for suffix in ("", "/verify"):
        data = client.get(PATH + suffix, headers=auth("admin")).json()
        assert data["enabled"] is False and "SANDBOX_BUDGET_BACKEND=redis" in data["message"]
    monkeypatch.setattr(app.state.budget_tracker, "backend", "redis")

    def fail(*args, **kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr(app.state.budget_tracker.client, "xtrim", fail)
    for suffix in ("", "/verify"):
        response = client.get(PATH + suffix, headers=auth("admin"))
        assert response.status_code == 503
        assert response.json()["detail"]["reason"] == "audit_view_unavailable"
