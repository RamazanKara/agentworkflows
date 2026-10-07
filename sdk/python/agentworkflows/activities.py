"""Register GatewayActivities.call on each team's Temporal worker."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Any
from urllib.parse import quote

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agentworkflows import GatewayError, _raise_for_status
from agentworkflows.workflows import Call


class GatewayActivities:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @activity.defn(name="agentworkflows.call")
    async def call(self, call: Call) -> dict[str, Any]:
        info = activity.info()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=120) as client:
                initialized = await client.put(f"/v1/workflow-runs/{info.workflow_run_id}", json=asdict(call.budget))
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
                else:
                    raise ApplicationError("Use WorkflowGateway.model or .tool.", non_retryable=True)
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
