"""Pooled PostgreSQL storage for retained gateway records."""

from __future__ import annotations

from time import time
from typing import Any
from uuid import uuid4

from psycopg.types.json import Json, Jsonb
from psycopg_pool import ConnectionPool

from app.audit import ChainHead
from app.settings import Settings
from app.storage_migrations import migrate


class PostgresStorage:
    backend = "postgres"

    def __init__(self, settings: Settings, pool: Any = None) -> None:
        self.settings = settings
        self.scope = settings.sandbox_budget_key_prefix
        self.pool = (
            pool
            if pool is not None
            else ConnectionPool(
                settings.storage_postgres_dsn,
                min_size=1,
                max_size=settings.storage_postgres_pool_size,
                timeout=settings.storage_postgres_timeout_seconds,
                open=False,
                kwargs={
                    "connect_timeout": max(2, int(settings.storage_postgres_timeout_seconds)),
                    "options": f"-c statement_timeout={int(settings.storage_postgres_timeout_seconds * 1000)}",
                },
                check=ConnectionPool.check_connection,
            )
        )

    def open(self) -> None:
        try:
            self.pool.open(wait=True, timeout=self.settings.storage_postgres_timeout_seconds)
            migrate(self.pool)
        except Exception:
            self.pool.close()
            raise

    def close(self) -> None:
        self.pool.close()

    def health(self) -> bool:
        with self.pool.connection() as connection:
            return connection.execute("SELECT 1").fetchone() == (1,)

    def get_settings(self, team: str) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM aw_team_settings WHERE scope = %s AND team = %s",
                (self.scope, team),
            ).fetchone()
            return row[0] if row else None

    def export_team(self, team: str) -> dict[str, Any]:
        with self.pool.connection() as connection:
            runs = connection.execute(
                "SELECT run_id, document, snapshot FROM aw_runs WHERE scope = %s AND team = %s "
                "AND (expires_at IS NULL OR expires_at > %s)",
                (self.scope, team, time()),
            ).fetchall()
            intents = connection.execute(
                "SELECT document FROM aw_start_intents WHERE scope = %s AND document->>'team_id' = %s",
                (self.scope, team),
            ).fetchall()
        return {
            "settings": self.get_settings(team),
            "runs": [
                {"run_id": run_id, "metadata": document, "snapshot": snapshot, "timeline": self.run_steps(team, run_id)}
                for run_id, document, snapshot in runs
            ],
            "start_intents": [row[0] for row in intents],
            "keys": [{k: v for k, v in row.items() if k != "sha256"} for row in self.list_keys(team)],
            "audit": list(self.audit_entries(team, None, True)),
        }

    def delete_team(self, team: str) -> None:
        with self.pool.connection() as connection:
            for table in ("aw_runs", "aw_api_keys", "aw_team_settings", "aw_audit_events"):
                from psycopg import sql

                connection.execute(
                    sql.SQL("DELETE FROM {} WHERE scope = %s AND team = %s").format(sql.Identifier(table)),
                    (self.scope, team),
                )
            connection.execute(
                "DELETE FROM aw_start_intents WHERE scope = %s AND document->>'team_id' = %s", (self.scope, team)
            )

    def change_settings(self, team: str, revision: int, document: dict[str, Any]) -> bool:
        with self.pool.connection() as connection:
            if revision == 0:
                row = connection.execute(
                    "INSERT INTO aw_team_settings VALUES (%s, %s, 1, %s) ON CONFLICT DO NOTHING RETURNING revision",
                    (self.scope, team, Jsonb(document)),
                ).fetchone()
            else:
                row = connection.execute(
                    "UPDATE aw_team_settings SET revision = revision + 1, document = %s "
                    "WHERE scope = %s AND team = %s AND revision = %s RETURNING revision",
                    (Jsonb(document), self.scope, team, revision),
                ).fetchone()
            return row is not None

    def create_key(self, record: dict[str, Any]) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO aw_api_keys VALUES (%s, %s, %s, %s, %s)",
                (self.scope, record["key_id"], record["team"], record["sha256"], Jsonb(record)),
            )

    def lookup_key(self, digest: str, now: float) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM aw_api_keys WHERE scope = %s AND digest = %s FOR UPDATE",
                (self.scope, digest),
            ).fetchone()
            if not row:
                return None
            record = row[0]
            if (
                record["revoked_at"] is None
                and (record["expires_at"] is None or record["expires_at"] > now)
                and (record["last_used_at"] is None or now - record["last_used_at"] >= 60)
            ):
                record["last_used_at"] = now
                connection.execute(
                    "UPDATE aw_api_keys SET document = %s WHERE scope = %s AND digest = %s",
                    (Jsonb(record), self.scope, digest),
                )
            return record

    def list_keys(self, team: str) -> list[dict[str, Any]]:
        with self.pool.connection() as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT document FROM aw_api_keys WHERE scope = %s AND team = %s",
                    (self.scope, team),
                ).fetchall()
            ]

    def change_key(self, team: str, key_id: str, project: str, changes: dict[str, Any]) -> Any:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM aw_api_keys WHERE scope = %s AND team = %s AND key_id = %s FOR UPDATE",
                (self.scope, team, key_id),
            ).fetchone()
            if not row or (project and row[0]["project"] != project):
                return None
            record = row[0]
            if record["revoked_at"] is not None:
                return "revoked"
            record.update(changes)
            connection.execute(
                "UPDATE aw_api_keys SET document = %s WHERE scope = %s AND key_id = %s",
                (Jsonb(record), self.scope, key_id),
            )
            return record

    def get_intent(self, workflow_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM aw_start_intents WHERE scope = %s AND workflow_id = %s "
                "AND (expires_at IS NULL OR expires_at > %s)",
                (self.scope, workflow_id, time()),
            ).fetchone()
            return row[0] if row else None

    def save_intent(self, workflow_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self.pool.connection() as connection:
            row = connection.execute(
                "INSERT INTO aw_start_intents (scope, workflow_id, document) VALUES (%s, %s, %s) "
                "ON CONFLICT (scope, workflow_id) DO UPDATE SET workflow_id = EXCLUDED.workflow_id RETURNING document",
                (self.scope, workflow_id, Jsonb(data)),
            ).fetchall()[0]
            return row[0]

    def get_run(self, team: str, run_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM aw_runs WHERE scope = %s AND team = %s AND run_id = %s "
                "AND (expires_at IS NULL OR expires_at > %s)",
                (self.scope, team, run_id, time()),
            ).fetchone()
            return row[0] if row else None

    def save_run(self, team: str, run_id: str, data: dict[str, Any]) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO aw_runs (scope, team, run_id, project, created_at, document) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (self.scope, team, run_id, data["project"], data["created_at"], Jsonb(data)),
            )

    def page_runs(self, team: str, project: str, offset: int, limit: int, position: Any = None) -> list:
        params: list[Any] = [self.scope, team, project, time()]
        condition = ""
        if position:
            condition = ' AND (created_at, run_id COLLATE "C") < (%s, %s)'
            params.extend([position.created_at, str(position.run_id)])
        params.extend([limit, offset])
        with self.pool.connection() as connection:
            return connection.execute(
                "SELECT run_id, created_at FROM aw_runs WHERE scope = %s AND team = %s AND project = %s "
                "AND (expires_at IS NULL OR expires_at > %s)"
                + condition
                + ' ORDER BY created_at DESC, run_id COLLATE "C" DESC LIMIT %s OFFSET %s',
                params,
            ).fetchall()

    def remove_run_index(self, team: str, project: str, run_id: str) -> None:
        # SQL indexes cannot outlive their records; retention deletes the row itself.
        pass

    def append_step(self, team: str, run_id: str, event: dict[str, Any]) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO aw_run_steps (scope, team, run_id, event) "
                "SELECT scope, team, run_id, %s FROM aw_runs WHERE scope = %s AND team = %s AND run_id = %s "
                "AND (expires_at IS NULL OR expires_at > %s)",
                (Json(event), self.scope, team, run_id, time()),
            )

    def run_steps(self, team: str, run_id: str) -> list[dict[str, Any]]:
        with self.pool.connection() as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT event FROM aw_run_steps JOIN aw_runs USING (scope, team, run_id) "
                    "WHERE scope = %s AND team = %s AND run_id = %s AND (expires_at IS NULL OR expires_at > %s) "
                    "ORDER BY id",
                    (self.scope, team, run_id, time()),
                ).fetchall()
            ]

    def get_snapshot(self, team: str, run_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT snapshot FROM aw_runs WHERE scope = %s AND team = %s AND run_id = %s "
                "AND (expires_at IS NULL OR expires_at > %s)",
                (self.scope, team, run_id, time()),
            ).fetchone()
            return row[0] if row else None

    def save_snapshot(self, team: str, run_id: str, snapshot: dict[str, Any]) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                "UPDATE aw_runs SET snapshot = %s WHERE scope = %s AND team = %s AND run_id = %s "
                "AND snapshot IS NULL AND (expires_at IS NULL OR expires_at > %s)",
                (Jsonb(snapshot), self.scope, team, run_id, time()),
            )

    def expire_run(self, team: str, run_id: str, deadline: int) -> int:
        with self.pool.connection() as connection:
            row = connection.execute(
                "UPDATE aw_runs SET expires_at = COALESCE(expires_at, %s) "
                "WHERE scope = %s AND team = %s AND run_id = %s RETURNING expires_at, document->>'workflow_id'",
                (deadline, self.scope, team, run_id),
            ).fetchone()
            if row:
                deadline = int(row[0])
                connection.execute(
                    "UPDATE aw_start_intents SET expires_at = LEAST(expires_at, %s) "
                    "WHERE scope = %s AND workflow_id = %s",
                    (deadline, self.scope, row[1]),
                )
            return deadline

    def append_audit(self, event: dict[str, Any], sequence: int, entry: dict[str, Any] | None) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO aw_audit_events (scope, team, chain_id, sequence, event, entry) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    self.scope,
                    event.get("sandbox_id"),
                    event["chain_id"],
                    sequence,
                    Json(event),
                    Json({key: value for key, value in entry.items() if key not in {"event", "chain_id", "sequence"}})
                    if entry is not None
                    else None,
                ),
            )
            self._save_head(connection, ChainHead(event["chain_id"], event["record_hash"], sequence))

    def audit_entries(self, team: str, cursor: str | None, oldest_first: bool) -> Any:
        params: list[Any] = [self.scope, team, time(), self.scope, team, self.settings.audit_view_retention_seconds]
        condition = ""
        if cursor:
            number, suffix = map(int, cursor.split("-"))
            if number > 9223372036854775807:
                number = 9223372036854775807
            condition = " AND id <= %s" if suffix else " AND id < %s"
            params.append(number)
        # A server cursor keeps one snapshot across retention without loading the whole archive.
        with self.pool.connection() as connection, connection.cursor(name="audit_" + uuid4().hex) as reader:
            reader.execute(
                "SELECT id, chain_id, sequence, event, entry FROM aw_audit_events WHERE scope = %s AND team = %s "
                "AND stored_at >= %s - COALESCE((SELECT (document->'retention'->>'audit_seconds')::int "
                "FROM aw_team_settings WHERE scope = %s AND team = %s), %s) AND entry IS NOT NULL"
                + condition
                + (" ORDER BY id" if oldest_first else " ORDER BY id DESC"),
                params,
            )
            while rows := reader.fetchmany(500):
                for number, chain_id, sequence, event, entry in rows:
                    yield {**entry, "id": f"{number}-0", "chain_id": chain_id, "sequence": sequence, "event": event}

    def load(self) -> ChainHead | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT chain_id, head, count FROM aw_audit_heads WHERE scope = %s ORDER BY updated_at DESC LIMIT 1",
                (self.scope,),
            ).fetchone()
            return ChainHead(*row) if row else None

    def _save_head(self, connection: Any, head: ChainHead) -> None:
        connection.execute(
            "INSERT INTO aw_audit_heads (scope, chain_id, head, count) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (scope, chain_id) DO UPDATE SET head = EXCLUDED.head, count = EXCLUDED.count, "
            "updated_at = clock_timestamp() WHERE aw_audit_heads.count < EXCLUDED.count",
            (self.scope, head.chain_id, head.head, head.count),
        )

    def save(self, head: ChainHead) -> None:
        with self.pool.connection() as connection:
            self._save_head(connection, head)

    def retain(self) -> None:
        with self.pool.connection() as connection:
            if not connection.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 714062002))",
                (self.scope,),
            ).fetchall()[0][0]:
                return
            now = time()
            connection.execute("DELETE FROM aw_runs WHERE scope = %s AND expires_at <= %s", (self.scope, now))
            connection.execute("DELETE FROM aw_start_intents WHERE scope = %s AND expires_at <= %s", (self.scope, now))
            connection.execute(
                "DELETE FROM aw_audit_events e WHERE e.scope = %s AND e.stored_at < %s - COALESCE("
                "(SELECT (document->'retention'->>'audit_seconds')::integer FROM aw_team_settings s "
                "WHERE s.scope = e.scope AND s.team = e.team), %s)",
                (self.scope, now, self.settings.audit_view_retention_seconds),
            )
            connection.execute(
                "DELETE FROM aw_audit_heads WHERE scope = %s AND updated_at < to_timestamp(%s) "
                "AND chain_id <> (SELECT chain_id FROM aw_audit_heads WHERE scope = %s "
                "ORDER BY updated_at DESC LIMIT 1)",
                (self.scope, now - self.settings.audit_view_retention_seconds, self.scope),
            )
