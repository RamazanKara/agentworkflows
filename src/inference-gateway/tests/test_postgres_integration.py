"""Optional real SQL checks: TEST_POSTGRES_DSN must allow creating a disposable schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from time import time
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from app.audit import AUDIT_GENESIS, advance_chain
from app.audit_view import check_entry
from app.postgres_storage import PostgresStorage
from app.settings import Settings
from app.storage_migrations import migrate
from psycopg import sql
from psycopg_pool import ConnectionPool


@pytest.fixture
def postgres():
    dsn = os.getenv("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set TEST_POSTGRES_DSN to run real PostgreSQL migration and storage checks")
    schema = "aw_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    pool = ConnectionPool(
        dsn,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={
            "options": f"-c search_path={schema} -c statement_timeout=5000",
        },
    )
    store = PostgresStorage(replace(Settings.from_env(), storage_backend="postgres", storage_postgres_dsn=dsn), pool)
    try:
        store.open()
        yield store
    finally:
        pool.close()
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_real_migrations_up_down_up_and_newer_schema_refusal(postgres):
    assert postgres.health()
    migrate(postgres.pool)
    migrate(postgres.pool, 0)
    migrate(postgres.pool, 0)
    with postgres.pool.connection() as connection:
        assert connection.execute("SELECT to_regclass('aw_runs')").fetchone() == (None,)
    migrate(postgres.pool)
    with postgres.pool.connection() as connection:
        connection.execute("INSERT INTO aw_schema_version VALUES (2)")
    with pytest.raises(RuntimeError):
        migrate(postgres.pool)
    with postgres.pool.connection() as connection:
        connection.execute("DELETE FROM aw_schema_version WHERE version = 2")


def test_real_settings_cas_keys_and_scope_isolation(postgres):
    def change(value):
        return postgres.change_settings("team", 0, {"revision": 1, "overrides": {"cost_limit_usd": value}})

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(change, range(4))) == 1
    assert not postgres.change_settings("team", 0, {"revision": 1})
    other = PostgresStorage(replace(postgres.settings, sandbox_budget_key_prefix="other"), postgres.pool)
    assert other.get_settings("team") is None
    key = {
        "key_id": "key",
        "team": "team",
        "sha256": "a" * 64,
        "role": "viewer",
        "project": "private",
        "expires_at": None,
        "revoked_at": None,
        "last_used_at": None,
    }
    postgres.create_key(key)
    assert other.lookup_key(key["sha256"], time()) is None
    assert postgres.lookup_key(key["sha256"], 100)["last_used_at"] == 100
    assert postgres.lookup_key(key["sha256"], 101)["last_used_at"] == 100
    assert postgres.change_key("other", "key", "", {"role": "admin"}) is None
    assert postgres.change_key("team", "key", "public", {"role": "admin"}) is None
    assert postgres.change_key("team", "key", "private", {"revoked_at": 102})["revoked_at"] == 102
    assert postgres.change_key("team", "key", "", {"role": "admin"}) == "revoked"
    assert postgres.lookup_key(key["sha256"], 200)["last_used_at"] == 100


def test_real_run_paging_snapshots_and_retention_cascade(postgres):
    created = time()
    ids = sorted(str(uuid4()) for _ in range(3))
    for run_id in ids:
        data = {"workflow_id": f"team/project/{run_id}", "project": "project", "created_at": created}
        assert postgres.save_intent(data["workflow_id"], data) == data
        postgres.save_run("team", run_id, data)
        postgres.append_step("team", run_id, {"value": -0.0})
    assert [row[0] for row in postgres.page_runs("team", "project", 0, 2)] == ids[::-1][:2]
    cursor = SimpleNamespace(created_at=created, run_id=ids[1])
    assert postgres.page_runs("team", "project", 0, 2, cursor) == [(ids[0], created)]
    postgres.save_snapshot("team", ids[0], {"status": "completed", "result": "answer"})
    postgres.save_snapshot("team", ids[0], {"status": "failed"})
    assert postgres.get_snapshot("team", ids[0])["status"] == "completed"
    assert postgres.get_run("other", ids[0]) is None
    deadline = int(time()) - 1
    assert postgres.expire_run("team", ids[0], deadline) == deadline
    assert postgres.expire_run("team", ids[0], deadline + 999) == deadline
    assert postgres.get_run("team", ids[0]) is None
    assert postgres.run_steps("team", ids[0]) == []
    postgres.retain()
    postgres.retain()
    with postgres.pool.connection() as connection:
        assert connection.execute("SELECT count(*) FROM aw_run_steps").fetchone() == (2,)
        assert connection.execute("SELECT count(*) FROM aw_start_intents").fetchone() == (2,)


def test_real_redis_import_dry_run_atomic_resume_and_conflict(postgres, monkeypatch):
    from app import redis_import

    from tests.test_redis_import import source_fixture

    client, source, _, head = source_fixture(postgres.settings)
    progress = []
    result = redis_import.import_redis(client, postgres, head=head, progress=progress.append)
    assert result["dry_run"] and result["inserted"] == 8
    assert postgres.get_settings("team") is None
    original = redis_import.import_record

    def interrupt(connection, scope, kind, identity, data, apply):
        result = original(connection, scope, kind, identity, data, apply)
        if apply and kind == "run":
            raise OSError("crash before run transaction committed")
        return result

    monkeypatch.setattr(redis_import, "import_record", interrupt)
    with pytest.raises(OSError):
        redis_import.import_redis(client, postgres, apply=True, head=head, progress=progress.append)
    assert postgres.get_run("team", "run") is None
    assert postgres.run_steps("team", "run") == []
    monkeypatch.setattr(redis_import, "import_record", original)
    redis_import.import_redis(client, postgres, apply=True, head=head, progress=progress.append)
    repeated = redis_import.import_redis(client, postgres, apply=True, head=head, progress=progress.append)
    assert repeated["existing"] == 8 and repeated["inserted"] == 0
    assert postgres.get_settings("team")["revision"] == 7
    assert len(postgres.run_steps("team", "run")) == 1
    assert postgres.lookup_key("a" * 64, time())["revoked_at"] == 4
    entries = list(postgres.audit_entries("team", None, True))
    assert len(entries) == 3 and all(check_entry(row, entries[i - 1] if i else None) is None
                                   for i, row in enumerate(entries))
    assert postgres.load() == head
    source[postgres.scope + ":team-settings:team"] = '{"revision":8,"overrides":{}}'
    with pytest.raises(ValueError, match="Destination conflict"):
        redis_import.import_redis(client, postgres, apply=True, head=head, progress=progress.append)
    assert postgres.get_settings("team")["revision"] == 7


def test_real_audit_hashes_restart_heads_paging_and_retention_boundary(postgres):
    previous = view_previous = AUDIT_GENESIS
    entries = []
    for sequence in range(1, 4):
        event = {
            "chain_id": "chain",
            "sandbox_id": "team",
            "event": "test",
            "ts": time(),
            "number": -0.0,
            "small": 1e-30,
            "text": "café",
            "sequence": sequence,
        }
        event["prev_hash"], event["record_hash"] = advance_chain(previous, event)
        previous = event["record_hash"]
        entry = {
            "chain_id": "chain",
            "sequence": sequence,
            "team_sequence": sequence,
            "record_hash": previous,
            "event": event,
        }
        entry["view_prev_hash"], entry["view_hash"] = advance_chain(view_previous, entry)
        view_previous = entry["view_hash"]
        postgres.append_audit(event, sequence, entry)
        entries.append(entry)
    rows = list(postgres.audit_entries("team", None, True))
    assert all(check_entry(row, rows[i - 1] if i else None) is None for i, row in enumerate(rows))
    assert len(list(postgres.audit_entries("team", rows[-1]["id"], False))) == 2
    assert list(postgres.audit_entries("other", None, False)) == []
    restarted = PostgresStorage(postgres.settings, postgres.pool)
    assert restarted.load().head == previous
    with postgres.pool.connection() as connection:
        connection.execute("UPDATE aw_audit_events SET stored_at = 0 WHERE sequence = 1")
    postgres.retain()
    retained = list(postgres.audit_entries("team", None, True))
    assert retained[0]["team_sequence"] == 2 and check_entry(retained[0], None) is None
    with postgres.pool.connection() as connection:
        connection.execute(
            "UPDATE aw_audit_events SET event = jsonb_set(event::jsonb, '{text}', '\"tampered\"')::json "
            "WHERE sequence = 3"
        )
    tampered = list(postgres.audit_entries("team", None, True))
    assert check_entry(tampered[1], tampered[0]) == "record_hash_mismatch"
