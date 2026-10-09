"""Version the existing Redis gateway state without rewriting retained runs or budgets."""

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from temporalio.service import RPCError, RPCStatusCode

SCHEMA_VERSION = 2
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


async def migrate_run_retention(app: FastAPI) -> bool:
    """Online TTL backfill: older metadata has no status, so consult Temporal."""
    from app.team_data import enter_team_request, leave_team_request
    from app.workflow_budget import redis_call
    from app.workflow_operations import RPC_TIMEOUT, temporal_client
    from app.workflow_retention import TERMINAL_STATES, retain_run

    if app.state.budget_tracker.backend != "redis":
        return True
    request = Request({"type": "http", "app": app, "headers": [], "path": "/v1/internal"})
    prefix = app.state.settings.sandbox_budget_key_prefix
    client = None
    complete = True
    cursor = 0
    while True:
        cursor, keys = await redis_call(request, "scan", cursor, match=f"{prefix}:workflow:*:metadata", count=100)
        for key in keys:
            raw = await redis_call(request, "get", key)
            if not raw:
                continue
            data = json.loads(raw)
            run_id = key.rsplit(":", 2)[1]
            request.state.sandbox_id = data["team_id"]
            try:
                entered = await enter_team_request(request)
            except HTTPException as exc:
                if exc.status_code == 409:
                    continue
                raise
            try:
                if client is None:
                    client = await temporal_client(app)
                handle = client.get_workflow_handle(data["workflow_id"], run_id=run_id)
                try:
                    description = await handle.describe(rpc_timeout=RPC_TIMEOUT)
                except RPCError as exc:
                    if exc.status != RPCStatusCode.NOT_FOUND:
                        raise
                    # Temporal already purged this execution; start its final Redis retention window now.
                    await retain_run(request, run_id, data, None)
                else:
                    if description.status.name.lower() in TERMINAL_STATES:
                        await retain_run(request, run_id, data, description.close_time)
            except (HTTPException, RPCError, OSError):
                complete = False
                logging.getLogger("uvicorn.error").warning("Run retention migration pending; check Temporal and Redis.")
            finally:
                if entered:
                    await leave_team_request(request)
        if cursor == 0:
            return complete
