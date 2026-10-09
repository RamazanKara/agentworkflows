import copy
import json
from time import time
from unittest.mock import MagicMock

import pytest
from app.audit import AUDIT_GENESIS, ChainHead, advance_chain
from app.postgres_storage import PostgresStorage
from app.redis_import import import_record, import_redis, source_records
from app.settings import Settings


def source_fixture(settings):
    prefix = settings.sandbox_budget_key_prefix
    deadline = int(time()) + 3600
    metadata = {"workflow_id": "team/default/run", "project": "default", "created_at": 1.0}
    key = {
        "key_id": "key",
        "team": "team",
        "sha256": "a" * 64,
        "name": "Revoked key",
        "role": "viewer",
        "project": None,
        "created_by": "admin",
        "created_at": 1,
        "expires_at": 99,
        "last_used_at": 3,
        "revoked_at": 4,
    }
    data = {
        f"{prefix}:workflow:team:run:metadata": json.dumps(metadata),
        f"{prefix}:workflow:team:run:retention": str(deadline),
        f"{prefix}:start:team/default/run": json.dumps({"input": {"topic": "hello"}}),
        f"{prefix}:team-settings:team": json.dumps({"revision": 7, "overrides": {"cost_limit_usd": 2}}),
    }
    entries = []
    previous, view = AUDIT_GENESIS, AUDIT_GENESIS
    for sequence in (1, 2, 3):
        event = {"chain_id": "chain", "sandbox_id": "team", "ts": 123.0, "value": -0.0, "text": "café"}
        event["prev_hash"], event["record_hash"] = advance_chain(previous, event)
        entry = {
            "chain_id": "chain",
            "sequence": sequence,
            "team_sequence": sequence,
            "record_hash": event["record_hash"],
            "event": event,
        }
        entry["view_prev_hash"], entry["view_hash"] = advance_chain(view, entry)
        entries.append((f"{int(time() * 1000)}-{sequence}", {"entry": json.dumps(entry)}))
        previous, view = event["record_hash"], entry["view_hash"]
    client = MagicMock()
    client.scan_iter.side_effect = lambda **_: iter([*data, f"{prefix}:audit:team", *data])
    client.get.side_effect = data.get
    client.expiretime.return_value = deadline
    client.lrange.return_value = [json.dumps({"receipt": "original", "cost": -0.0})]
    client.hgetall.return_value = {"key": json.dumps(key)}
    client.xrange.side_effect = lambda key, min, count: entries if min == "-" else []
    return client, data, entries, ChainHead("chain", previous, 3)


def test_source_is_read_only_deduplicates_scan_and_preserves_records():
    settings = Settings.from_env()
    client, _, entries, _ = source_fixture(settings)
    records = list(source_records(client, settings))
    assert len(records) == 7
    audit = [row[2] for row in records if row[0] == "audit"]
    assert audit[0]["entry"] == json.loads(entries[0][1]["entry"])
    assert audit[0]["stored_at"] == int(entries[0][0].split("-")[0]) / 1000
    key = next(row[2]["document"] for row in records if row[0] == "key")
    assert key["revoked_at"] == 4 and key["last_used_at"] == 3
    assert not any(call[0] in {"set", "eval", "xtrim", "delete", "expire"} for call in client.mock_calls)


@pytest.mark.parametrize("damage", ["edit", "gap", "reorder", "team"])
def test_tampering_and_missing_interior_audit_events_abort_before_sql(damage):
    settings = Settings.from_env()
    client, _, entries, _ = source_fixture(settings)
    if damage == "gap":
        entries.pop(1)
    elif damage == "reorder":
        entries.reverse()
    else:
        entry = json.loads(entries[0][1]["entry"])
        entry["event"]["text" if damage == "edit" else "sandbox_id"] = "changed"
        entries[0][1]["entry"] = json.dumps(entry)
    store = PostgresStorage(settings, MagicMock())
    with pytest.raises(ValueError):
        import_redis(client, store, apply=True)
    store.pool.connection.assert_not_called()


def test_dry_run_resume_and_changed_source(monkeypatch):
    settings = Settings.from_env()
    client, data, _, head = source_fixture(settings)
    store = PostgresStorage(settings, MagicMock())
    destination = {}
    committed = []

    def save(connection, scope, kind, identity, document, apply):
        key = (scope, kind, identity)
        if key in destination:
            assert document == destination[key]
            return False
        if apply:
            if len(committed) == 3:
                raise OSError("interrupted")
            destination[key] = copy.deepcopy(document)
            committed.append(key)
        return True

    monkeypatch.setattr("app.redis_import.import_record", save)
    output = []
    assert import_redis(client, store, head=head, progress=output.append)["inserted"] == 8
    assert not destination
    with pytest.raises(OSError):
        import_redis(client, store, apply=True, head=head, progress=output.append)
    assert len(destination) == 3
    committed.clear()
    committed.extend([None] * 4)
    assert import_redis(client, store, apply=True, head=head, progress=output.append)["existing"] == 3
    assert import_redis(client, store, apply=True, head=head, progress=output.append)["existing"] == 8
    assert output[-1]["phase"] == "verified"
    original = client.scan_iter.side_effect

    def change_source(**kwargs):
        if client.scan_iter.call_count % 2 == 0:
            data[settings.sandbox_budget_key_prefix + ":team-settings:changed"] = '{"revision":1}'
        return original(**kwargs)

    client.scan_iter.reset_mock()
    client.scan_iter.side_effect = change_source
    with pytest.raises(ValueError, match="changed"):
        import_redis(client, store, apply=True, head=head, progress=output.append)


def test_destination_conflicts_fail_preflight_without_overwriting():
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = ({"revision": 2},)
    with pytest.raises(ValueError, match="Destination conflict"):
        import_record(connection, "scope", "settings", "team", {"document": {"revision": 1}}, True)
    assert len(connection.execute.call_args_list) == 1


def test_audit_inserts_keep_json_numbers_and_original_storage_time():
    settings = Settings.from_env()
    client, _, _, _ = source_fixture(settings)
    kind, identity, data = next(row for row in source_records(client, settings) if row[0] == "audit")
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = None
    assert import_record(connection, "scope", kind, identity, data, True)
    query, params = connection.execute.call_args.args
    assert "stored_at" in query
    assert params[4] == data["stored_at"]
    assert json.dumps(params[5].obj) == json.dumps(data["entry"]["event"])


def test_head_conflicts_and_retained_prefix_boundaries():
    settings = Settings.from_env()
    client, _, entries, head = source_fixture(settings)
    entries.pop(0)
    store = PostgresStorage(settings, MagicMock())
    store.pool.connection.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
    assert import_redis(client, store, head=head, progress=lambda _: None)["retained_boundaries"] == 1
    with pytest.raises(ValueError, match="head disagrees"):
        import_redis(client, store, head=ChainHead("chain", "wrong", 3))
