"""Per-sandbox request-rate limiting with in-memory and Redis fixed-window backends.

This is a short-window throttle (requests per ``rate_limit_window_seconds``) that is
distinct from the long-window cumulative sandbox budget: it bounds burst abuse and
credential brute-forcing rather than total spend. It reuses the budget backend choice
(``sandbox_budget_backend``) and, for Redis, the same connection and key prefix, so no
new infrastructure is required. The in-memory backend is process-local, so with N
gateway replicas the effective limit is N x the configured value (use Redis for an
accurate cluster-wide limit) - the same trade-off the in-memory budget carries.
"""

from __future__ import annotations

from threading import Lock
from time import time
from typing import Any, Protocol

from app.budget import BudgetBackendError
from app.settings import Settings

try:  # redis is an optional dependency; only present when the redis backend is used.
    from redis.exceptions import RedisError as _RedisError

    _RATE_LIMIT_BACKEND_ERRORS: tuple[type[BaseException], ...] = (_RedisError, OSError)
except ImportError:  # pragma: no cover - redis always installed in the gateway image
    _RATE_LIMIT_BACKEND_ERRORS = (OSError,)


class RateLimiter(Protocol):
    """Protocol for rate limiters that admit or reject a key within a window."""

    settings: Settings

    def check(self, key: str, settings: Settings | None = None) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` for one request against ``key``."""


def _limit_and_window(settings: Settings) -> tuple[int, int]:
    return settings.rate_limit_requests_per_window, settings.rate_limit_window_seconds


class InMemoryRateLimiter:
    """Process-local fixed-window rate limiter guarded by a lock."""

    backend = "memory"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def check(self, key: str, settings: Settings | None = None) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` for one request against ``key``."""
        resolved = settings or self.settings
        limit, window = _limit_and_window(resolved)
        if limit <= 0 or window <= 0:
            return True, 0
        now = time()
        with self._lock:
            window_start, count = self._windows.get(key, (now, 0))
            if now - window_start >= window:
                window_start, count = now, 0
            count += 1
            self._windows[key] = (window_start, count)
            if count > limit:
                retry_after = max(1, int(window - (now - window_start)))
                return False, retry_after
        return True, 0


REDIS_RATE_LIMIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  -- A fresh window, or a counter that lost its expiry: (re)arm it so a stuck key can
  -- never turn into a permanent lockout for the sandbox.
  redis.call('EXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""


class RedisRateLimiter:
    """Distributed fixed-window rate limiter backed by Redis INCR/EXPIRE.

    The increment and the expiry run as one script: a single round trip on every governed
    request instead of two or three, and no window in which a crash between INCR and
    EXPIRE can leave a counter that never expires.
    """

    backend = "redis"

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        if client is None:
            try:
                import redis
            except ImportError as exc:  # pragma: no cover - exercised only without redis installed
                raise RuntimeError("redis package is required when SANDBOX_BUDGET_BACKEND=redis") from exc
            client = redis.Redis.from_url(
                settings.sandbox_budget_redis_url,
                decode_responses=True,
                socket_timeout=settings.sandbox_budget_redis_timeout_seconds,
                socket_connect_timeout=settings.sandbox_budget_redis_timeout_seconds,
                # A lost write reply must not replay accounting mutations.
                retry=redis.retry.Retry(redis.backoff.NoBackoff(), 0),
            )
        self.client = client

    def _key(self, key: str) -> str:
        return f"{self.settings.sandbox_budget_key_prefix}:ratelimit:{key}"

    def check(self, key: str, settings: Settings | None = None) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` using a Redis fixed-window counter.

        Raises :class:`~app.budget.BudgetBackendError` when Redis is unreachable so the
        caller can return a 503 (retry later) instead of surfacing a driver error as an
        unhandled 500 - the same contract the budget tracker on this backend keeps.
        """
        resolved = settings or self.settings
        limit, window = _limit_and_window(resolved)
        if limit <= 0 or window <= 0:
            return True, 0
        try:
            count, ttl = (int(value) for value in self.client.eval(REDIS_RATE_LIMIT_SCRIPT, 1, self._key(key), window))
        except _RATE_LIMIT_BACKEND_ERRORS as exc:
            raise BudgetBackendError("rate limit backend is unavailable") from exc
        if count > limit:
            retry_after = ttl if ttl > 0 else window
            return False, retry_after
        return True, 0


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """Return a Redis or in-memory rate limiter per the configured budget backend."""
    if settings.sandbox_budget_backend == "redis":
        return RedisRateLimiter(settings)
    return InMemoryRateLimiter(settings)
