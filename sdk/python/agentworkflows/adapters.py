"""Framework clients sharing one activity's governed gateway context."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from agentworkflows import _raise_for_status


class AgentContext:
    def __init__(self, client: httpx.AsyncClient, api_key: str, headers: dict[str, str]) -> None:
        self.client = client
        self.api_key = api_key
        self.headers = headers
        self.sequence = 0
        self.framework_client: Any = None
        client.event_hooks["request"].append(self._request)
        client.event_hooks["response"].append(self._response)

    async def _request(self, request: Any) -> None:
        self.sequence += 1
        request.headers.update(self.headers)
        request.headers["X-Workflow-Step-ID"] = f"{self.headers['X-Workflow-Step-ID']}/{self.sequence}"

    async def _response(self, response: Any) -> None:
        if response.is_error:
            await response.aread()
            _raise_for_status(response)

    def _framework_http(self) -> Any:
        import httpx2

        if self.framework_client is None:
            self.framework_client = httpx2.AsyncClient(
                timeout=120,
                headers={"Authorization": f"Bearer {self.api_key}"},
                event_hooks={"request": [self._request], "response": [self._response]},
            )
        return self.framework_client

    async def aclose(self) -> None:
        if self.framework_client is not None:
            await self.framework_client.aclose()

    def openai(self) -> Any:
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            base_url=str(self.client.base_url).rstrip("/") + "/v1",
            api_key=self.api_key,
            http_client=self._framework_http(),
            max_retries=0,
        )

    def anthropic(self) -> Any:
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(
            base_url=str(self.client.base_url), api_key=self.api_key, http_client=self._framework_http(), max_retries=0
        )

    def agents_model(self, model: str) -> Any:
        from agents import OpenAIChatCompletionsModel

        return OpenAIChatCompletionsModel(model=model, openai_client=self.openai())

    async def tool(self, name: str, arguments: dict[str, Any]) -> Any:
        response = await self.client.post(f"/v1/tools/{quote(name, safe='')}/call", json={"arguments": arguments})
        _raise_for_status(response)
        return response.json()["result"]
