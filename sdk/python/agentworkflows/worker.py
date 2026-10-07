"""Worker startup shared by generated projects and packaged examples."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any

import httpx
from temporalio.client import Client
from temporalio.service import RPCError
from temporalio.worker import Worker

from agentworkflows.activities import GatewayActivities
from agentworkflows.adapters import AgentContext


async def serve(
    workflows: Sequence[type],
    *,
    agents: Mapping[str, Callable[[AgentContext, dict[str, Any]], Awaitable[Any]]] | None = None,
) -> None:
    key = os.getenv("AGENTWORKFLOWS_API_KEY")
    if not key:
        raise ValueError("Set AGENTWORKFLOWS_API_KEY to your team's worker key (demo-worker for Compose).")
    team = os.getenv("AGENTWORKFLOWS_TEAM", "demo")
    queue = os.getenv("TEMPORAL_TASK_QUEUE", f"{team}-workflows")
    client = await Client.connect(
        os.getenv("TEMPORAL_ADDRESS", "localhost:7233"), namespace=os.getenv("TEMPORAL_NAMESPACE", "default")
    )
    activities = GatewayActivities(os.getenv("AGENTWORKFLOWS_URL", "http://127.0.0.1:8080"), key, agents=agents)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    worker = Worker(
        client,
        task_queue=queue,
        workflows=workflows,
        activities=[activities.call],
        max_concurrent_activities=2,
        max_concurrent_workflow_tasks=2,
        graceful_shutdown_timeout=timedelta(seconds=180),
    )

    async def probe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=2)
            path = line.split()[1] if len(line.split()) == 3 else b""
            ready = worker.is_running and not stopping.is_set()
            if path == b"/readyz" and ready:
                try:
                    async with asyncio.timeout(3), httpx.AsyncClient(timeout=2) as http:
                        response = await http.get(f"{activities.base_url}/readyz")
                        ready = response.status_code == 200 and await client.service_client.check_health()
                except Exception:
                    ready = False
            status = 200 if path == b"/healthz" or (path == b"/readyz" and ready) else 503
            writer.write(f"HTTP/1.1 {status} OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(probe, "0.0.0.0", 8081)
    previous = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stopping.set))
        async with worker:
            print(f"Worker ready on {queue}. Start a run with agentworkflows runs start; stop with Ctrl+C.", flush=True)
            await stopping.wait()
    finally:
        server.close()
        await server.wait_closed()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run_worker(workflows: Sequence[type]) -> None:
    """Run a project's workflows with environment-based gateway and Temporal settings."""
    try:
        asyncio.run(serve(workflows))
    except KeyboardInterrupt:
        pass
    except ValueError as exc:
        raise SystemExit(f"agentworkflows worker: {exc}") from None
    except (RPCError, RuntimeError, OSError):
        raise SystemExit(
            "agentworkflows worker: cannot start the worker. Check TEMPORAL_ADDRESS and namespace, "
            "start the Compose stack, and check that each class has @workflow.defn and @workflow.run."
        ) from None
