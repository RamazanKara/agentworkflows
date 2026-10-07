"""Version the existing Redis gateway state without rewriting retained runs or budgets."""

from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MIGRATIONS = Path(__file__).with_name("migrations")


def migrate(client: Any, prefix: str) -> None:
    key = f"{prefix}:schema-version"
    current = client.get(key)
    if current is not None and current not in {str(version) for version in range(SCHEMA_VERSION + 1)}:
        raise RuntimeError("Unsupported gateway schema; use the matching image or restore a pre-upgrade backup")
    # Redis serializes each migration with its version write, including concurrent pod starts.
    for version in range(int(current or 0) + 1, SCHEMA_VERSION + 1):
        script = (MIGRATIONS / f"{version:03d}.lua").read_text(encoding="utf-8")
        client.eval(script, 1, key)
