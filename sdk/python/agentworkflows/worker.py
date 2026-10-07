"""Worker startup shared by generated projects and packaged examples."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

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
    async with Worker(
        client,
        task_queue=queue,
        workflows=workflows,
        activities=[activities.call],
        max_concurrent_activities=2,
        max_concurrent_workflow_tasks=2,
    ):
        print(f"Worker ready on {queue}. Start a run with agentworkflows runs start; stop with Ctrl+C.", flush=True)
        await asyncio.Event().wait()


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
