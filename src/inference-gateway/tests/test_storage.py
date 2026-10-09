# ruff: noqa: F811
import asyncio
import copy
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.audit_view import check_entry
from app.main import create_app
from app.postgres_storage import PostgresStorage
from app.settings import Settings
from app.storage import RedisStorage, storage_call
from app.storage_migrations import migrate
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from psycopg import OperationalError
from psycopg_pool import PoolTimeout

from tests.test_audit_view import record
from tests.test_managed_keys import AuthRedis
from tests.test_teams import auth, start, team_gateway  # noqa: F401


class RecordStore(RedisStorage):
    """Separate fake record database; the live budget Redis must not receive these writes."""

    backend = "postgres"

    def __init__(self, settings):
        super().__init__(
            SimpleNamespace(
                settings=settings,
                budget_tracker=SimpleNamespace(
                    backend="redis",
                    client=AuthRedis(),
                ),
            )
        )
        self.snapshots = {}

    def get_snapshot(self, team, run_id):
        return copy.deepcopy(self.snapshots.get((team, run_id)))

    def save_snapshot(self, team, run_id, snapshot):
        self.snapshots.setdefault((team, run_id), copy.deepcopy(snapshot))


def test_records_route_to_selected_store_and_terminal_history_survives_temporal(team_gateway):
    client, app = team_gateway
    app.state.storage = RecordStore(app.state.settings)
    saved = client.patch(
        "/v1/team/settings",
        headers={**auth("admin"), "If-Match": "0"},
        json={
            "fields": {"cost_limit_usd": 9},
        },
    )
    assert saved.status_code == 200, saved.text
    assert (
        client.patch(
            "/v1/team/settings",
            headers={**auth("admin"), "If-Match": "0"},
            json={
                "fields": {"cost_limit_usd": 10},
            },
        ).status_code
        == 409
    )
    run_id = start(client).json()["run_id"]
    execution = next(iter(app.state.temporal_client.executions.values()))
    execution.status = "COMPLETED"
    first = client.get(f"/v1/workflow-runs/{run_id}", headers=auth("viewer"))
    assert first.status_code == 200, first.text
    expected = first.json()
    app.state.temporal_client = SimpleNamespace()
    assert client.get(f"/v1/workflow-runs/{run_id}", headers=auth("viewer")).json() == expected
    page = client.get("/v1/workflow-runs", headers=auth("viewer")).json()
    assert page["runs"][0]["run_id"] == run_id
    assert "result" not in page["runs"][0]
    assert client.get(f"/v1/workflow-runs/{run_id}", headers=auth("other")).status_code == 404
    assert client.get("/v1/team/settings", headers=auth("admin")).json()["revision"] == 1
    assert not any(":metadata" in key or ":team-settings:" in key for key in app.state.budget_tracker.client.data)
    audit = client.get("/v1/team/audit/verify", headers=auth("admin"))
    assert audit.status_code == 200 and audit.json()["ok"]


def test_key_metadata_and_revocation_use_selected_store(team_gateway):
    client, app = team_gateway
    app.state.storage = RecordStore(app.state.settings)
    created = client.post("/v1/team/keys", headers=auth("admin"), json={"name": "automation", "role": "viewer"})
    assert created.status_code == 201, created.text
    key = created.json()
    headers = {"Authorization": "Bearer " + key["key"]}
    assert client.get("/v1/team", headers=headers).status_code == 200
    listed = client.get("/v1/team/keys", headers=auth("admin")).json()["keys"]
    assert listed[0]["last_used_at"] is not None and "sha256" not in listed[0] and "key" not in listed[0]
    assert client.delete("/v1/team/keys/" + key["key_id"], headers=auth("admin")).status_code == 200
    assert client.get("/v1/team", headers=headers).status_code == 401
    assert not any(":keys:" in name for name in app.state.budget_tracker.client.data)


def test_sql_errors_fail_closed_without_redis_fallback(team_gateway):
    _, app = team_gateway
    request = Request({"type": "http", "app": app})
    app.state.storage = SimpleNamespace(
        backend="postgres", get_settings=MagicMock(side_effect=OperationalError("down"))
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(storage_call(request, "get_settings", "team"))
    assert error.value.status_code == 503
    assert error.value.detail["reason"] == "storage_unavailable"


@pytest.mark.parametrize("failure", [OperationalError, PoolTimeout])
def test_postgres_failure_after_temporal_start_recovers_same_run(team_gateway, monkeypatch, failure):
    _, app = team_gateway
    app.state.storage = RecordStore(app.state.settings)
    original = app.state.storage.save_run
    attempts = []

    def fail_once(*args):
        attempts.append(args)
        if len(attempts) == 1:
            raise failure("postgresql://user:private-password@private-host/db")
        return original(*args)

    monkeypatch.setattr(app.state.storage, "save_run", fail_once)
    client, _ = team_gateway
    request_id = str(uuid4())
    first = start(client, request_id=request_id)
    assert first.status_code == 503 and "private-password" not in first.text
    recovered = start(client, request_id=request_id)
    assert recovered.status_code == 201 and len(app.state.temporal_client.executions) == 1
    assert start(client, request_id=request_id).json()["run_id"] == recovered.json()["run_id"]


def test_storage_configuration_and_lazy_pool(monkeypatch):
    assert Settings.from_env().storage_backend == "redis"
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("STORAGE_POSTGRES_DSN", "postgresql://localhost/gateway")
    settings = Settings.from_env()
    assert settings.storage_backend == "postgres"
    with pytest.raises(ValueError, match="DSN"):
        replace(settings, storage_postgres_dsn="")
    for field, value in [
        ("storage_backend", "sqlite"),
        ("storage_postgres_pool_size", 0),
        ("storage_postgres_timeout_seconds", float("nan")),
    ]:
        with pytest.raises(ValueError):
            replace(settings, **{field: value})
    pool = MagicMock()
    factory = MagicMock(return_value=pool)
    monkeypatch.setattr("app.postgres_storage.ConnectionPool", factory)
    app = create_app(settings)
    assert app.state.storage.backend == "postgres"
    assert factory.call_args.kwargs["open"] is False
    assert factory.call_args.kwargs["max_size"] == 5
    pool.open.assert_not_called()


class MigrationPool:
    def __init__(self):
        self.versions = []
        self.statements = []
        self.fail = False

    @contextmanager
    def connection(self):
        before = list(self.versions)
        try:
            yield self
        except Exception:
            self.versions = before
            raise

    def execute(self, sql, params=()):
        self.statements.append(sql)
        if sql.startswith("INSERT INTO aw_schema_version"):
            self.versions.append(params[0])
        if sql.startswith("DELETE FROM aw_schema_version"):
            self.versions.remove(params[0])
        if self.fail and sql.startswith("CREATE TABLE aw_team_settings"):
            raise OperationalError("DDL failed")
        return self

    def fetchall(self):
        return [(version,) for version in self.versions]


def test_migrations_up_down_idempotence_lock_and_rollback():
    pool = MigrationPool()
    migrate(pool)
    migrate(pool)
    assert pool.versions == [1]
    assert sum(sql.startswith("CREATE TABLE aw_team_settings") for sql in pool.statements) == 1
    assert pool.statements[0].startswith("SELECT pg_advisory_xact_lock")
    migrate(pool, 0)
    migrate(pool, 0)
    assert pool.versions == []
    assert sum(sql.startswith("DROP TABLE aw_run_steps") for sql in pool.statements) == 1
    pool.fail = True
    with pytest.raises(OperationalError):
        migrate(pool)
    assert pool.versions == []
    pool.versions = [1, 2]
    with pytest.raises(RuntimeError, match="Unsupported"):
        migrate(pool)


def test_postgres_pool_health_failure_cleanup_and_parameter_binding(monkeypatch):
    pool = MagicMock()
    connection = pool.connection.return_value.__enter__.return_value
    connection.execute.return_value.fetchone.return_value = (1,)
    store = PostgresStorage(Settings.from_env(), pool)
    assert store.health()
    store.get_settings("team' OR true--")
    sql, params = connection.execute.call_args.args
    assert "team'" not in sql and params[1] == "team' OR true--"
    monkeypatch.setattr("app.postgres_storage.migrate", MagicMock(side_effect=RuntimeError("schema")))
    with pytest.raises(RuntimeError):
        store.open()
    pool.close.assert_called_once()


def test_postgres_lifecycle_readiness_and_retention(monkeypatch):
    store = MagicMock(backend="postgres")
    store.load.return_value = None
    store.health.return_value = True
    monkeypatch.setattr("app.postgres_storage.PostgresStorage", lambda settings: store)
    app = create_app(replace(Settings.from_env(), storage_backend="postgres", storage_postgres_dsn="postgresql://test"))
    app.state.runtime_client.health = AsyncMock(return_value={"status": "ok"})
    with TestClient(app) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["dependencies"]["gateway_store"] == {"status": "ok", "backend": "postgres"}
        store.health.side_effect = OperationalError("down")
        assert client.get("/readyz").status_code == 503
        assert client.get("/healthz").status_code == 200
    store.open.assert_called_once()
    store.retain.assert_called()
    store.close.assert_called_once()
    assert store.mock_calls[-1][0] == "close"


def test_retention_lock_scopes_deletion_and_keeps_settings_and_keys():
    pool = MagicMock()
    connection = pool.connection.return_value.__enter__.return_value
    store = PostgresStorage(Settings.from_env(), pool)
    connection.execute.return_value.fetchall.return_value = [(False,)]
    store.retain()
    assert len(connection.execute.call_args_list) == 1
    connection.execute.reset_mock()
    connection.execute.return_value.fetchall.return_value = [(True,)]
    store.retain()
    deletes = [call.args for call in connection.execute.call_args_list if call.args[0].startswith("DELETE")]
    assert len(deletes) == 4
    assert all("scope = %s" in sql and params[0] == store.scope for sql, params in deletes)
    assert not any("DELETE FROM aw_api_keys" in sql or "DELETE FROM aw_team_settings" in sql for sql, _ in deletes)


def test_audit_sql_roundtrip_keeps_hash_payload_and_original_verification(team_gateway):
    _, app = team_gateway
    pool = MagicMock()
    connection = pool.connection.return_value.__enter__.return_value
    store = PostgresStorage(app.state.settings, pool)
    app.state.storage = store
    event = record(app, amount=-0.0, small=1e-30, text="café")
    sql, params = connection.execute.call_args_list[0].args
    assert "INSERT INTO aw_audit_events" in sql
    assert params[4].obj == event
    assert "event" not in params[5].obj
    reader = connection.cursor.return_value.__enter__.return_value
    reader.fetchmany.side_effect = [[(1, params[2], params[3], params[4].obj, params[5].obj)], []]
    entry, = store.audit_entries("team", None, True)
    assert check_entry(entry, None) is None
    entry["event"]["text"] = "edited"
    assert check_entry(entry, None) == "record_hash_mismatch"


def test_audit_paging_streams_batches_and_releases_pool_on_early_close():
    pool = MagicMock()
    connection = pool.connection.return_value.__enter__.return_value
    reader = connection.cursor.return_value.__enter__.return_value
    reader.fetchmany.side_effect = [[(1, "chain", 1, {"ts": 1}, {})], [(2, "chain", 2, {"ts": 2}, {})], []]
    store = PostgresStorage(Settings.from_env(), pool)
    entries = store.audit_entries("team", "3-0", False)
    assert next(entries)["id"] == "1-0"
    entries.close()
    reader.fetchmany.assert_called_once()
    connection.cursor.return_value.__exit__.assert_called_once()
    pool.connection.return_value.__exit__.assert_called_once()
