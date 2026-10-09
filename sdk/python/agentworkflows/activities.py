"""Register GatewayActivities.call on each team's Temporal worker."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from datetime import timedelta
from typing import Any
from urllib.parse import quote

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agentworkflows import GatewayError, _raise_for_status
from agentworkflows.adapters import AgentContext
from agentworkflows.workflows import Call


class GatewayActivities:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        agents: Mapping[str, Callable[[AgentContext, dict[str, Any]], Awaitable[Any]]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.agents = agents or {}

    @activity.defn(name="agentworkflows.trigger")
    async def trigger(self, request: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url, headers={"Authorization": f"Bearer {self.api_key}"}, timeout=30
        ) as client:
            if request.get("run_id"):
                response = await client.get(f"/v1/workflow-runs/{request['run_id']}")
            else:
                response = await client.post(
                    f"/v1/workflow-triggers/{quote(request['workflow'], safe='')}/"
                    f"{quote(request['trigger'], safe='')}/fire",
                    json={"firing_id": request["firing_id"]},
                )
            try:
                _raise_for_status(response)
            except GatewayError as exc:
                if exc.reason == "trigger_paused":
                    return {"paused": True}
                raise ApplicationError(
                    "Scheduled launch unavailable; inspect gateway trigger receipts.",
                    non_retryable=exc.status_code < 500 and exc.status_code != 429,
                ) from None
            return response.json()

    @activity.defn(name="agentworkflows.call")
    async def call(self, call: Call) -> dict[str, Any]:
        info = activity.info()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=120) as client:
                if call.kind == "approval_waiting":
                    response = await client.post(
                        f"/v1/workflow-runs/{info.workflow_run_id}/approval-waiting", json=call.payload or None
                    )
                    # Legacy direct-Temporal runs are not indexed in the console.
                    if response.status_code == 404:
                        return {"queued": False, "policy_version": 1, "approval_required": True,
                                "required_approvals": 1, "approval_timeout_seconds": 604800}
                    _raise_for_status(response)
                    return response.json()
                initialized = await client.put(
                    f"/v1/workflow-runs/{info.workflow_run_id}",
                    json={**asdict(call.budget), "workflow": info.workflow_type},
                    headers={"X-Workflow-ID": info.workflow_id or ""},
                )
                _raise_for_status(initialized)
                headers = {
                    "X-Workflow-Run-ID": info.workflow_run_id or "",
                    "X-Workflow-Step-ID": info.activity_id,
                    "X-Data-Classification": call.data_classification,
                }
                if call.kind == "model":
                    path = "/v1/chat/completions"
                elif call.kind == "tool":
                    path = f"/v1/tools/{quote(call.tool, safe='')}/call"
                elif call.kind == "agent":
                    handler = self.agents.get(call.tool)
                    if handler is None:
                        raise ApplicationError(
                            "Register this agent in GatewayActivities(agents={...}).", non_retryable=True
                        )
                    context = AgentContext(client, self.api_key, headers)
                    try:
                        return {"result": await handler(context, call.payload)}
                    finally:
                        await context.aclose()
                elif call.kind == "container":
                    from agentworkflows.containers import run_container

                    return await run_container(client, call, headers)
                else:
                    raise ApplicationError(
                        "Use WorkflowGateway.model, .tool, .agent, or .container.", non_retryable=True
                    )
                response = await client.post(path, json=call.payload, headers=headers)
                _raise_for_status(response)
                result: dict[str, Any] = response.json()
                return result
        except GatewayError as exc:
            retryable = exc.status_code in {429, 500, 502, 503, 504}
            if exc.reason in {"workflow_store_required", "provider_not_configured", "tool_not_configured"}:
                retryable = False
            retry_after = exc.response.headers.get("Retry-After", "")
            delay = timedelta(seconds=int(retry_after)) if retryable and retry_after.isdigit() else None
            raise ApplicationError(
                f"{exc}. Gateway request ID: {exc.request_id or 'unavailable'}",
                type=exc.reason or "GatewayError",
                non_retryable=not retryable,
                next_retry_delay=delay,
            ) from None
        except httpx.HTTPError:
            raise ApplicationError(
                "Cannot reach the gateway; check AGENTWORKFLOWS_URL and gateway health.", type="GatewayUnavailable"
            ) from None
