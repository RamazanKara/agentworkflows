"""Server-side response store for the stateful Responses API subset (ADR 0012).

Holds stored `/v1/responses` objects so `store: true` responses can be retrieved and chained
via `previous_response_id`. Unlike the redacted audit chain (ADR 0006), a stored response
contains the caller's **raw** conversation content, so this store is opt-in
(`RESPONSES_STORE_ENABLED`), tenant-scoped, retention-bounded (TTL), and deletable on demand.

Two backends implement the `ResponseStore` protocol: `MemoryResponseStore` (process-local,
tests/local) and `RedisResponseStore` (shared, TTL-expiring). Records are keyed by tenant so a
tenant can neither read nor delete another tenant's responses.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from time import time
from typing import Any, Protocol

try:  # redis is an optional dependency; only present when the redis backend is used.
    from redis.exceptions import RedisError as _RedisError

    _RESPONSE_BACKEND_ERRORS: tuple[type[BaseException], ...] = (_RedisError, OSError)
except ImportError:  # pragma: no cover - redis always installed in the gateway image
    _RESPONSE_BACKEND_ERRORS = (OSError,)


class ResponseStoreError(RuntimeError):
    """Raised when the response store backend (e.g. Redis) is unreachable."""


@dataclass
class StoredResponse:
    """A persisted Responses object plus what is needed to retrieve and chain it."""

    id: str
    tenant: str
    created_at: int
    model: str
    body: dict[str, Any]  # the full Responses object returned by GET /v1/responses/{id}
    input_items: list[Any]  # this turn's input items (GET /v1/responses/{id}/input_items)
    messages: list[dict[str, Any]]  # full chat conversation incl. this reply (for chaining)
    previous_response_id: str | None = None
    data_classification: str = "internal"


class ResponseStore(Protocol):
    """Tenant-scoped storage for stateful Responses objects."""

    backend: str

    def create(self, record: StoredResponse) -> None: ...

    def get(self, tenant: str, response_id: str) -> StoredResponse | None: ...

    def delete(self, tenant: str, response_id: str) -> bool: ...


class MemoryResponseStore:
    """Process-local response store with per-record TTL expiry."""

    backend = "memory"

    def __init__(self, retention_seconds: int) -> None:
        self._retention = retention_seconds
        self._lock = Lock()
        self._store: OrderedDict[str, tuple[float, StoredResponse]] = OrderedDict()

    @staticmethod
    def _key(tenant: str, response_id: str) -> str:
        return f"{tenant}\x00{response_id}"

    def create(self, record: StoredResponse) -> None:
        with self._lock:
            now = time()
            # Reclaim unread records without scanning every live conversation on each write.
            while self._store:
                expires_at, _ = next(iter(self._store.values()))
                if expires_at > now:
                    break
                self._store.popitem(last=False)
            key = self._key(record.tenant, record.id)
            self._store[key] = (now + self._retention, record)
            self._store.move_to_end(key)

    def get(self, tenant: str, response_id: str) -> StoredResponse | None:
        key = self._key(tenant, response_id)
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires_at, record = item
            if expires_at <= time():
                self._store.pop(key, None)
                return None
            return record

    def delete(self, tenant: str, response_id: str) -> bool:
        with self._lock:
            item = self._store.pop(self._key(tenant, response_id), None)
            return item is not None and item[0] > time()


class RedisResponseStore:
    """Shared response store backed by Redis string keys with a retention TTL."""

    backend = "redis"

    def __init__(self, settings: Any, client: Any | None = None) -> None:
        self.prefix = settings.responses_key_prefix
        self.retention = settings.responses_retention_seconds
        if client is None:
            try:
                import redis
            except ImportError as exc:  # pragma: no cover - redis always installed in the image
                raise RuntimeError("redis package is required when RESPONSES_STORE_BACKEND=redis") from exc
            client = redis.Redis.from_url(
                settings.responses_redis_url,
                decode_responses=True,
                socket_timeout=settings.responses_redis_timeout_seconds,
                socket_connect_timeout=settings.responses_redis_timeout_seconds,
            )
        self.client = client

    def _key(self, tenant: str, response_id: str) -> str:
        return f"{self.prefix}:{tenant}:{response_id}"

    def export_team(self, tenant: str) -> list[dict[str, Any]]:
        try:
            result = []
            for key in self.client.scan_iter(match=f"{self.prefix}:{tenant}:*", count=500):
                raw = self.client.get(key)
                if raw:
                    result.append(json.loads(raw))
            return result
        except _RESPONSE_BACKEND_ERRORS as exc:
            raise ResponseStoreError("response store backend is unavailable") from exc

    def delete_team(self, tenant: str) -> None:
        try:
            for key in self.client.scan_iter(match=f"{self.prefix}:{tenant}:*", count=500):
                self.client.delete(key)
        except _RESPONSE_BACKEND_ERRORS as exc:
            raise ResponseStoreError("response store backend is unavailable") from exc

    def create(self, record: StoredResponse) -> None:
        try:
            self.client.set(self._key(record.tenant, record.id), json.dumps(_to_dict(record)), ex=self.retention)
        except _RESPONSE_BACKEND_ERRORS as exc:
            raise ResponseStoreError("response store backend is unavailable") from exc

    def get(self, tenant: str, response_id: str) -> StoredResponse | None:
        try:
            raw = self.client.get(self._key(tenant, response_id))
        except _RESPONSE_BACKEND_ERRORS as exc:
            raise ResponseStoreError("response store backend is unavailable") from exc
        return _from_json(raw)

    def delete(self, tenant: str, response_id: str) -> bool:
        try:
            return bool(self.client.delete(self._key(tenant, response_id)))
        except _RESPONSE_BACKEND_ERRORS as exc:
            raise ResponseStoreError("response store backend is unavailable") from exc


def _to_dict(record: StoredResponse) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant": record.tenant,
        "created_at": record.created_at,
        "model": record.model,
        "body": record.body,
        "input_items": record.input_items,
        "messages": record.messages,
        "previous_response_id": record.previous_response_id,
        "data_classification": record.data_classification,
    }


def _from_json(raw: str | None) -> StoredResponse | None:
    if not raw:
        return None
    return StoredResponse(**json.loads(raw))


def build_response_store(settings: Any) -> ResponseStore:
    """Return a Redis-backed or in-memory response store per the configured backend."""
    if settings.responses_store_backend == "redis":
        return RedisResponseStore(settings)
    return MemoryResponseStore(settings.responses_retention_seconds)
