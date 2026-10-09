"""Offline, restartable import of retained Redis records into the gateway SQL store."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterator
from typing import Any

import redis
from psycopg import Error as PostgresError
from psycopg.types.json import Json, Jsonb

from app.audit import build_chain_store
from app.audit_view import check_entry
from app.postgres_storage import PostgresStorage
from app.settings import Settings


def source_records(client: Any, settings: Settings) -> Iterator[tuple[str, str, dict[str, Any]]]:
    prefix = settings.sandbox_budget_key_prefix
    # SCAN can repeat keys even with writers stopped.
    for key in sorted(set(client.scan_iter(match=f"{prefix}:*", count=500))):
        if not key.startswith(prefix + ":"):
            continue
        suffix = key[len(prefix) + 1 :]
        if suffix.startswith("team-settings:"):
            raw = client.get(key)
            if raw:
                yield "settings", suffix[14:], {"document": json.loads(raw)}
        elif suffix.startswith("start:"):
            raw = client.get(key)
            expiry = client.expiretime(key)
            if raw and expiry != -2:
                yield "intent", suffix[6:], {"document": json.loads(raw), "expires_at": expiry if expiry >= 0 else None}
        elif suffix.startswith("workflow:") and suffix.endswith(":metadata"):
            raw = client.get(key)
            expiry = client.expiretime(key)
            if not raw or expiry == -2:
                continue
            team, run_id = suffix[9:-9].split(":")
            base = key[:-9]
            retention = client.get(base + ":retention")
            deadlines = [int(value) for value in (expiry, retention) if value is not None and int(value) >= 0]
            yield (
                "run",
                f"{team}:{run_id}",
                {
                    "team": team,
                    "run_id": run_id,
                    "document": json.loads(raw),
                    "expires_at": min(deadlines) if deadlines else None,
                    "steps": [json.loads(row) for row in client.lrange(base + ":timeline", 0, -1)],
                },
            )
        elif suffix.startswith("audit:"):
            team = suffix[6:]
            previous: dict[str, dict[str, Any]] = {}
            cursor = "-"
            while rows := client.xrange(key, min=cursor, count=500):
                for stream_id, fields in rows:
                    entry = json.loads(fields["entry"])
                    reason = check_entry(entry, previous.get(entry["chain_id"]))
                    if reason or entry["event"].get("sandbox_id") != team:
                        raise ValueError(f"Invalid audit chain for {team} at {stream_id}: {reason or 'team mismatch'}")
                    previous[entry["chain_id"]] = entry
                    yield (
                        "audit",
                        f"{entry['chain_id']}:{entry['sequence']}",
                        {
                            "team": team,
                            "stored_at": int(stream_id.split("-")[0]) / 1000,
                            "entry": entry,
                        },
                    )
                cursor = "(" + rows[-1][0]
    for key_id, raw in sorted(client.hgetall(f"{prefix}:keys:records").items()):
        document = json.loads(raw)
        if document["key_id"] != key_id:
            raise ValueError("Managed key identity mismatch")
        yield "key", key_id, {"document": document}


def same(left: Any, right: Any) -> bool:
    # JSON equality must also preserve float encodings covered by receipt hashes.
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def import_record(connection: Any, scope: str, kind: str, identity: str, data: dict[str, Any], apply: bool) -> bool:
    params: tuple[Any, ...] = (scope, identity)
    if kind == "settings":
        query = "SELECT document FROM aw_team_settings WHERE scope = %s AND team = %s"
        expected = (data["document"],)
        insert = "INSERT INTO aw_team_settings VALUES (%s, %s, %s, %s)"
        values = (*params, data["document"]["revision"], Jsonb(data["document"]))
    elif kind == "key":
        query = "SELECT document FROM aw_api_keys WHERE scope = %s AND key_id = %s"
        expected = (data["document"],)
        insert = "INSERT INTO aw_api_keys VALUES (%s, %s, %s, %s, %s)"
        document = data["document"]
        values = (*params, document["team"], document["sha256"], Jsonb(document))
        collision = connection.execute(
            "SELECT key_id FROM aw_api_keys WHERE scope = %s AND digest = %s", (scope, document["sha256"])
        ).fetchone()
        if collision and collision[0] != identity:
            raise ValueError("Destination contains a conflicting key digest")
    elif kind == "intent":
        query = "SELECT document, expires_at FROM aw_start_intents WHERE scope = %s AND workflow_id = %s"
        expected = (data["document"], data["expires_at"])
        insert = "INSERT INTO aw_start_intents (scope, workflow_id, document, expires_at) VALUES (%s, %s, %s, %s)"
        values = (*params, Jsonb(data["document"]), data["expires_at"])
    elif kind == "run":
        params = (scope, data["team"], data["run_id"])
        query = "SELECT document, expires_at FROM aw_runs WHERE scope = %s AND team = %s AND run_id = %s"
        expected = (data["document"], data["expires_at"])
        insert = (
            "INSERT INTO aw_runs (scope, team, run_id, document, expires_at, project, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)"
        )
        values = (
            *params,
            Jsonb(data["document"]),
            data["expires_at"],
            data["document"]["project"],
            data["document"]["created_at"],
        )
    elif kind == "head":
        query = "SELECT head, count FROM aw_audit_heads WHERE scope = %s AND chain_id = %s"
        expected = (data["head"], data["count"])
        insert = "INSERT INTO aw_audit_heads (scope, chain_id, head, count) VALUES (%s, %s, %s, %s)"
        values = (*params, *expected)
    else:
        entry = data["entry"]
        params = (scope, entry["chain_id"], entry["sequence"])
        query = (
            "SELECT team, stored_at, event, entry FROM aw_audit_events "
            "WHERE scope = %s AND chain_id = %s AND sequence = %s"
        )
        projection = {key: value for key, value in entry.items() if key not in {"event", "chain_id", "sequence"}}
        expected = (data["team"], data["stored_at"], entry["event"], projection)
        insert = (
            "INSERT INTO aw_audit_events (scope, chain_id, sequence, team, stored_at, event, entry) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)"
        )
        values = (*params, data["team"], data["stored_at"], Json(entry["event"]), Json(projection))
    row = connection.execute(query, params).fetchone()
    if row is not None:
        # SQL double precision returns integral deadlines as floats.
        if kind in {"run", "intent"}:
            row = (row[0], int(row[1]) if row[1] is not None else None)
        matches = same(row, expected) if kind == "audit" else row == expected
        if not matches:
            raise ValueError(f"Destination conflict: {kind} {identity}; no existing record was overwritten")
        if kind == "run":
            steps = connection.execute(
                "SELECT event FROM aw_run_steps WHERE scope = %s AND team = %s AND run_id = %s ORDER BY id", params
            ).fetchall()
            if not same([row[0] for row in steps], data["steps"]):
                raise ValueError(f"Destination timeline conflict: {identity}")
        return False
    if apply:
        connection.execute(insert, values)
        if kind == "run":
            for event in data["steps"]:
                connection.execute(
                    "INSERT INTO aw_run_steps (scope, team, run_id, event) VALUES (%s, %s, %s, %s)",
                    (*params, Json(event)),
                )
    return True


def import_redis(
    client: Any,
    store: PostgresStorage,
    *,
    apply: bool = False,
    head: Any = None,
    progress: Callable[[dict[str, Any]], None] = print,
) -> dict[str, Any]:
    records = []
    for record in source_records(client, store.settings):
        records.append(record)
        if len(records) % 100 == 0:
            progress({"phase": "read", "records": len(records)})
    heads: dict[str, dict[str, Any]] = {}
    head_times: dict[str, float] = {}
    receipts: dict[tuple[str, int], dict[str, Any]] = {}
    boundaries = 0
    view_hashes = {data["entry"]["view_hash"] for kind, _, data in records if kind == "audit"}
    for kind, _, data in records:
        if kind != "audit":
            continue
        entry = data["entry"]
        key = (entry["chain_id"], entry["sequence"])
        if key in receipts:
            raise ValueError("Duplicate source audit identity")
        receipts[key] = entry["event"]
        boundaries += int(entry["team_sequence"] > 1 and entry["view_prev_hash"] not in view_hashes)
        if entry["sequence"] > heads.get(entry["chain_id"], {}).get("count", 0):
            heads[entry["chain_id"]] = {"head": entry["record_hash"], "count": entry["sequence"]}
            head_times[entry["chain_id"]] = data["stored_at"]
    for (chain, sequence), event in receipts.items():
        previous = receipts.get((chain, sequence - 1))
        if previous and event["prev_hash"] != previous["record_hash"]:
            raise ValueError("Broken process audit chain")
    if head is not None:
        retained = heads.get(head.chain_id)
        if retained and (
            retained["count"] > head.count or (retained["count"] == head.count and retained["head"] != head.head)
        ):
            raise ValueError("Persisted audit head disagrees with retained receipts")
        heads[head.chain_id] = {"head": head.head, "count": head.count}
    order = sorted(
        heads, key=lambda chain: (head is not None and chain == head.chain_id, head_times.get(chain, 0), chain)
    )
    records.extend(("head", chain, heads[chain]) for chain in order)
    result = {
        "dry_run": not apply,
        "records": len(records),
        "inserted": 0,
        "existing": 0,
        "audit_checked": len(receipts),
        "retained_boundaries": boundaries,
    }
    # One lock per scope serializes import processes; application writers must be stopped.
    with store.pool.connection() as lock:
        lock.autocommit = True
        lock.execute("SELECT pg_advisory_lock(hashtextextended(%s, 714062003))", (store.scope,))
        try:
            for index, (kind, identity, data) in enumerate(records, 1):
                with lock.transaction():
                    pending = import_record(lock, store.scope, kind, identity, data, False)
                result["inserted" if pending else "existing"] += 1
                if index % 100 == 0:
                    progress({"phase": "preflight", "checked": index, "total": len(records)})
            progress({"phase": "preflight", **result})
            if apply:
                # Re-read before writing to catch an accidentally running source gateway.
                original = [row for row in records if row[0] != "head"]
                if not same(original, list(source_records(client, store.settings))):
                    raise ValueError("Redis changed during preflight; stop writers and retry")
                for index, (kind, identity, data) in enumerate(records, 1):
                    with lock.transaction():
                        import_record(lock, store.scope, kind, identity, data, True)
                    if index % 100 == 0:
                        progress({"phase": "import", "committed": index, "total": len(records)})
                for kind, identity, data in records:
                    with lock.transaction():
                        if import_record(lock, store.scope, kind, identity, data, False):
                            raise ValueError("Destination record disappeared during verification")
                for team in {data["team"] for kind, _, data in records if kind == "audit"}:
                    previous = {}
                    rows = lock.execute(
                        "SELECT chain_id, sequence, event, entry FROM aw_audit_events "
                        "WHERE scope = %s AND team = %s AND entry IS NOT NULL ORDER BY id",
                        (store.scope, team),
                    )
                    for chain_id, sequence, event, projection in rows:
                        entry = {**projection, "chain_id": chain_id, "sequence": sequence, "event": event}
                        reason = check_entry(entry, previous.get(entry["chain_id"]))
                        if reason:
                            raise ValueError(f"Imported audit verification failed: {reason}")
                        previous[entry["chain_id"]] = entry
                if not same(original, list(source_records(client, store.settings))):
                    raise ValueError("Redis changed during import; keep writers stopped and rerun preflight")
        finally:
            lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 714062003))", (store.scope,))
            lock.autocommit = False
    progress({"phase": "verified" if apply else "dry-run", **result})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Import quiesced Redis history; never deletes or overwrites records.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="Verify source hashes and destination conflicts without writes"
    )
    mode.add_argument("--apply", action="store_true", help="Import after stopping ALL gateway and worker writers")
    args = parser.parse_args()
    store = None
    client = None
    try:
        settings = Settings.from_env()
        if not settings.storage_postgres_dsn:
            raise ValueError("Set STORAGE_POSTGRES_DSN to the separate gateway database")
        store = PostgresStorage(settings)
        # Dry-run never creates tables or runs migrations; initialize an empty SQL store first.
        store.pool.open(wait=True, timeout=settings.storage_postgres_timeout_seconds)
        client = redis.Redis.from_url(
            settings.sandbox_budget_redis_url,
            decode_responses=True,
            socket_timeout=settings.sandbox_budget_redis_timeout_seconds,
            socket_connect_timeout=settings.sandbox_budget_redis_timeout_seconds,
        )
        head = build_chain_store(settings).load()
        import_redis(
            client, store, apply=args.apply, head=head, progress=lambda value: print(json.dumps(value), flush=True)
        )
        return 0
    except (ValueError, KeyError, TypeError, PostgresError, redis.RedisError, OSError):
        # Connection errors can contain credentials; record neither DSNs nor source payloads.
        print(
            "Import failed: source verification, destination conflict, or store unavailable. "
            "Keep writers stopped, inspect the stores and rerun --dry-run. No records were overwritten.",
            file=sys.stderr,
        )
        return 1
    finally:
        if client is not None:
            client.close()
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
