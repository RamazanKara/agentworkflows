"""Gateway records, independent of live Redis accounting and Temporal execution."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from fastapi import HTTPException, Request
from psycopg import Error as PostgresError
from redis.exceptions import RedisError


class Storage(Protocol):
    backend: str

    def export_team(self, team: str) -> dict[str, Any]: ...
    def delete_team(self, team: str) -> None: ...

    def get_settings(self, team: str) -> dict[str, Any] | None: ...
    def change_settings(self, team: str, revision: int, document: dict[str, Any]) -> bool: ...
    def create_key(self, record: dict[str, Any]) -> None: ...
    def lookup_key(self, digest: str, now: float) -> dict[str, Any] | None: ...
    def list_keys(self, team: str) -> list[dict[str, Any]]: ...
    def change_key(self, team: str, key_id: str, project: str, changes: dict[str, Any]) -> Any: ...
    def get_intent(self, workflow_id: str) -> dict[str, Any] | None: ...
    def save_intent(self, workflow_id: str, data: dict[str, Any]) -> dict[str, Any]: ...
    def get_run(self, team: str, run_id: str) -> dict[str, Any] | None: ...
    def save_run(self, team: str, run_id: str, data: dict[str, Any]) -> None: ...
    def page_runs(self, team: str, project: str, offset: int, limit: int, position: Any = None) -> list: ...
    def remove_run_index(self, team: str, project: str, run_id: str) -> None: ...
    def append_step(self, team: str, run_id: str, event: dict[str, Any]) -> None: ...
    def run_steps(self, team: str, run_id: str) -> list[dict[str, Any]]: ...
    def get_snapshot(self, team: str, run_id: str) -> dict[str, Any] | None: ...
    def save_snapshot(self, team: str, run_id: str, snapshot: dict[str, Any]) -> None: ...
    def expire_run(self, team: str, run_id: str, deadline: int) -> int: ...
    def append_audit(self, event: dict[str, Any], sequence: int, entry: dict[str, Any] | None) -> None: ...
    def audit_entries(self, team: str, cursor: str | None, oldest_first: bool) -> Any: ...


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


async def storage_call(request: Request, method: str, *args: Any) -> Any:
    try:
        return await asyncio.to_thread(getattr(get_storage(request), method), *args)
    except (RedisError, PostgresError, OSError) as exc:
        if get_storage(request).backend == "redis":
            raise HTTPException(
                503,
                detail={
                    "reason": "workflow_store_unavailable",
                    "message": "Workflow budget Redis is unavailable; retry later.",
                },
            ) from exc
        raise HTTPException(
            503,
            detail={
                "reason": "storage_unavailable",
                "message": "Gateway storage is unavailable. Retry after recovery.",
            },
        ) from exc


class RedisStorage:
    backend = "redis"

    def __init__(self, state: Any) -> None:
        self.state = state

    @property
    def client(self) -> Any:
        if self.state.budget_tracker.backend != "redis":
            raise HTTPException(
                503,
                detail={
                    "reason": "workflow_store_unavailable",
                    "message": "Configure SANDBOX_BUDGET_BACKEND=redis.",
                },
            )
        return self.state.budget_tracker.client

    @property
    def prefix(self) -> str:
        return self.state.settings.sandbox_budget_key_prefix

    def run_key(self, team: str, run_id: str) -> str:
        return f"{self.prefix}:workflow:{team}:{run_id}"

    def export_team(self, team: str) -> dict[str, Any]:
        runs = []
        cursor = 0
        while True:
            cursor, keys = self.client.scan(cursor, match=f"{self.prefix}:workflow:{team}:*:metadata", count=500)
            for key in keys:
                raw = self.client.get(key)
                if raw:
                    run_id = key.removeprefix(f"{self.prefix}:workflow:{team}:").removesuffix(":metadata")
                    runs.append(
                        {
                            "run_id": run_id,
                            "metadata": json.loads(raw),
                            "timeline": self.run_steps(team, run_id),
                        }
                    )
            if not cursor:
                break
        intents: list[dict[str, Any]] = []
        cursor = 0
        while True:
            cursor, keys = self.client.scan(cursor, match=f"{self.prefix}:start:{team}/*", count=500)
            intents.extend(json.loads(raw) for key in keys if (raw := self.client.get(key)))
            if not cursor:
                break
        return {
            "settings": self.get_settings(team),
            "runs": runs,
            "start_intents": intents,
            "keys": [{k: v for k, v in row.items() if k != "sha256"} for row in self.list_keys(team)],
            "audit": list(self.audit_entries(team, None, True)),
        }

    def delete_team(self, team: str) -> None:
        for record in self.list_keys(team):
            self.client.hdel(f"{self.prefix}:keys:records", record["key_id"])
            self.client.hdel(f"{self.prefix}:keys:digests", record["sha256"])
        for key in (
            f"{self.prefix}:team-settings:{team}",
            f"{self.prefix}:keys:team:{team}",
            f"{self.prefix}:audit:{team}",
        ):
            self.client.delete(key)

    def get_settings(self, team: str) -> dict[str, Any] | None:
        raw = self.client.get(f"{self.prefix}:team-settings:{team}")
        return json.loads(raw) if raw else None

    def change_settings(self, team: str, revision: int, document: dict[str, Any]) -> bool:
        from app.team_settings import CHANGE

        return bool(
            self.client.eval(
                CHANGE,
                1,
                f"{self.prefix}:team-settings:{team}",
                revision,
                json.dumps(document, allow_nan=False),
            )
        )

    def create_key(self, record: dict[str, Any]) -> None:
        from app.managed_keys import CREATE

        self.client.eval(
            CREATE,
            3,
            f"{self.prefix}:keys:records",
            f"{self.prefix}:keys:digests",
            f"{self.prefix}:keys:team:{record['team']}",
            record["key_id"],
            record["sha256"],
            json.dumps(record),
        )

    def lookup_key(self, digest: str, now: float) -> dict[str, Any] | None:
        from app.managed_keys import LOOKUP

        raw = self.client.eval(LOOKUP, 2, f"{self.prefix}:keys:records", f"{self.prefix}:keys:digests", digest, now)
        return json.loads(raw) if raw else None

    def list_keys(self, team: str) -> list[dict[str, Any]]:
        ids = self.client.smembers(f"{self.prefix}:keys:team:{team}")
        rows = self.client.hmget(f"{self.prefix}:keys:records", sorted(ids)) if ids else []
        return [json.loads(row) for row in rows if row]

    def change_key(self, team: str, key_id: str, project: str, changes: dict[str, Any]) -> Any:
        from app.managed_keys import CHANGE

        raw = self.client.eval(CHANGE, 1, f"{self.prefix}:keys:records", key_id, team, project, json.dumps(changes))
        return json.loads(raw) if raw and raw != "revoked" else raw

    def get_intent(self, workflow_id: str) -> dict[str, Any] | None:
        raw = self.client.get(f"{self.prefix}:start:{workflow_id}")
        return json.loads(raw) if raw else None

    def save_intent(self, workflow_id: str, data: dict[str, Any]) -> dict[str, Any]:
        key = f"{self.prefix}:start:{workflow_id}"
        self.client.setnx(key, json.dumps(data))
        return json.loads(self.client.get(key))

    def get_run(self, team: str, run_id: str) -> dict[str, Any] | None:
        raw = self.client.get(self.run_key(team, run_id) + ":metadata")
        return json.loads(raw) if raw else None

    def save_run(self, team: str, run_id: str, data: dict[str, Any]) -> None:
        self.client.setnx(self.run_key(team, run_id) + ":metadata", json.dumps(data))
        self.client.zadd(f"{self.prefix}:runs:{team}:{data['project']}", {run_id: data["created_at"]})

    def page_runs(self, team: str, project: str, offset: int, limit: int, position: Any = None) -> list:
        from app.workflow_operations import RUN_PAGE

        key = f"{self.prefix}:runs:{team}:{project}"
        if position:
            values = self.client.eval(RUN_PAGE, 1, key, position.created_at, str(position.run_id), limit - 1)
            return [(values[i], float(values[i + 1])) for i in range(0, len(values), 2)]
        return self.client.zrevrange(key, offset, offset + limit - 1, withscores=True)

    def remove_run_index(self, team: str, project: str, run_id: str) -> None:
        self.client.zrem(f"{self.prefix}:runs:{team}:{project}", run_id)

    def append_step(self, team: str, run_id: str, event: dict[str, Any]) -> None:
        self.client.rpush(self.run_key(team, run_id) + ":timeline", json.dumps(event))

    def run_steps(self, team: str, run_id: str) -> list[dict[str, Any]]:
        return [json.loads(row) for row in self.client.lrange(self.run_key(team, run_id) + ":timeline", 0, -1)]

    def get_snapshot(self, team: str, run_id: str) -> dict[str, Any] | None:
        return None

    def save_snapshot(self, team: str, run_id: str, snapshot: dict[str, Any]) -> None:
        pass

    def expire_run(self, team: str, run_id: str, deadline: int) -> int:
        return deadline

    def append_audit(self, event: dict[str, Any], sequence: int, entry: dict[str, Any] | None) -> None:
        from app.audit_view import APPEND, MAX_EVENTS
        from app.team_data import retention_values

        if entry is not None:
            self.client.eval(
                APPEND,
                1,
                f"{self.prefix}:audit:{event['sandbox_id']}",
                json.dumps(entry, sort_keys=True),
                MAX_EVENTS,
                retention_values(self.state.settings, self.get_settings(event["sandbox_id"]))["audit_seconds"],
            )

    def audit_entries(self, team: str, cursor: str | None, oldest_first: bool) -> Any:
        from app.audit_view import READ_BATCH, time
        from app.team_data import retention_values

        key = f"{self.prefix}:audit:{team}"
        retention = retention_values(self.state.settings, self.get_settings(team))["audit_seconds"]
        cutoff = max(0, int((time() - retention) * 1000))
        self.client.xtrim(key, minid=f"{cutoff}-0", approximate=False)
        tail = self.client.xrevrange(key, count=1)
        if not tail:
            return
        upper = f"({cursor}" if cursor else tail[0][0]
        while rows := (
            self.client.xrange(key, max=upper)
            if oldest_first
            else self.client.xrevrange(key, max=upper, count=READ_BATCH)
        ):
            for stream_id, fields in rows:
                yield {**json.loads(fields["entry"]), "id": stream_id}
            if oldest_first:
                return
            upper = f"({rows[-1][0]}"
