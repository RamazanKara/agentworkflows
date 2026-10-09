"""Transactional, versioned gateway SQL migrations; independent of Temporal's schema."""

from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MIGRATIONS = Path(__file__).with_name("sql")


def migrate(pool: Any, target: int = SCHEMA_VERSION) -> None:
    if not 0 <= target <= SCHEMA_VERSION:
        raise ValueError("Unsupported gateway storage schema target")
    with pool.connection() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(714062001)")
        connection.execute("CREATE TABLE IF NOT EXISTS aw_schema_version (version integer PRIMARY KEY)")
        rows = connection.execute("SELECT version FROM aw_schema_version ORDER BY version").fetchall()
        versions = [row[0] for row in rows]
        if versions != list(range(1, len(versions) + 1)) or len(versions) > SCHEMA_VERSION:
            raise RuntimeError("Unsupported gateway storage schema; upgrade the gateway before starting")
        current = len(versions)
        for version in range(current + 1, target + 1):
            connection.execute((MIGRATIONS / f"{version:03d}.up.sql").read_text(encoding="utf-8"))
            connection.execute("INSERT INTO aw_schema_version VALUES (%s)", (version,))
        for version in range(current, target, -1):
            connection.execute((MIGRATIONS / f"{version:03d}.down.sql").read_text(encoding="utf-8"))
            connection.execute("DELETE FROM aw_schema_version WHERE version = %s", (version,))
