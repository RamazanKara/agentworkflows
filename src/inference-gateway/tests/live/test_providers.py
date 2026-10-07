"""Explicit, paid acceptance tests. Not collected by the default gateway test target."""

import json
import logging
import os

import httpx
import pytest
from app.main import create_app
from app.policy import ModelRoute, ModelRoutingPolicy
from fastapi.testclient import TestClient
from tests.gateway_support import _tool_settings

PROVIDERS = {
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
    "anthropic": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1"),
    "azure-openai": ("AZURE_OPENAI_API_KEY", ""),
    "bedrock": ("AWS_BEARER_TOKEN_BEDROCK", ""),
    "vertex": ("VERTEX_ACCESS_TOKEN", ""),
}
pytestmark = pytest.mark.skipif(
    os.getenv("AGENTWORKFLOWS_LIVE_PROVIDERS") != "1" or bool(os.getenv("CI")),
    reason="Requires explicit local opt-in; live provider calls are disabled in CI",
)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("case", ["chat", "streaming", "tools", "fallback"])
def test_live_provider(provider, case, caplog, monkeypatch):
    key, default_url = PROVIDERS[provider]
    credential = os.getenv(key, "")
    if not credential or credential in {"compose-fake-only", "fixture", "test"}:
        pytest.skip(f"No real {key} in environment")
    prefix = "LIVE_" + provider.upper().replace("-", "_")
    model = os.getenv(f"{prefix}_MODEL")
    url = os.getenv(f"{prefix}_BASE_URL", default_url)
    prices = [os.getenv(f"{prefix}_{direction}_USD_PER_1K") for direction in ("INPUT", "OUTPUT")]
    assert model and url and all(prices), f"Set {prefix}_MODEL, _BASE_URL, _INPUT_USD_PER_1K and _OUTPUT_USD_PER_1K"
    route = ModelRoute(
        "live",
        provider,
        base_url=url,
        upstream_model=model,
        credential_env=key,
        input_usd_per_1k_tokens=float(prices[0]),
        output_usd_per_1k_tokens=float(prices[1]),
    )
    assert route.input_usd_per_1k_tokens > 0 and route.output_usd_per_1k_tokens > 0
    app = create_app(
        _tool_settings(
            model_id="live",
            allowed_models=("live", "unavailable"),
            sandbox_budget_enabled=True,
            allow_streaming=True,
            runtime_max_retries=0,
            request_timeout_seconds=60,
        )
    )
    app.state.model_routing_policy = ModelRoutingPolicy((ModelRoute("unavailable", "vllm", fallbacks=("live",)), route))
    # Deterministic failure before the first provider call; the fallback itself uses the real transport.
    runtime = app.state.runtime_client
    runtime.policy = app.state.model_routing_policy
    original = runtime._client_instance().send

    async def send(request, **kwargs):
        if request.url.host == "vllm":
            return httpx.Response(503, request=request)
        return await original(request, **kwargs)

    monkeypatch.setattr(runtime._client, "send", send)
    payload = {
        "model": "unavailable" if case == "fallback" else "live",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "Reply with hello."}],
    }
    if case == "streaming":
        payload.update(stream=True, stream_options={"include_usage": True})
    if case == "tools":
        payload.update(
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "parameters": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": "echo"}},
        )
        payload["messages"][0]["content"] = "Call echo with text hello."
    caplog.set_level(logging.INFO, logger="agentworkflows.audit")
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 200, f"{provider} {case}: HTTP {response.status_code}"
        if case == "streaming":
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
            assert "[DONE]" in response.text and not any("error" in event for event in events)
            assert any(
                choice.get("delta", {}).get("content") for event in events for choice in event.get("choices", [])
            )
            usages = [event["usage"] for event in events if event.get("usage")]
            assert usages, "Provider stream did not report usage"
            usage = usages[-1]
        else:
            reply = response.json()
            usage = reply["usage"]
            message = reply["choices"][0]["message"]
            if case == "tools":
                tool = message["tool_calls"][0]
                assert tool["function"]["name"] == "echo"
                assert json.loads(tool["function"]["arguments"])["text"]
            else:
                assert message["content"]
        assert usage["total_tokens"] > 0
        expected = (
            usage["prompt_tokens"] * route.input_usd_per_1k_tokens
            + usage["completion_tokens"] * route.output_usd_per_1k_tokens
        ) / 1000
        report = client.get("/v1/usage").json()["providers"][provider]
        assert report["total_tokens"] == usage["total_tokens"]
        assert report["estimated_cost"] == pytest.approx(expected)
    receipts = [json.loads(row.message) for row in caplog.records if row.name == "agentworkflows.audit"]
    receipt = next(row for row in reversed(receipts) if row.get("event") == "inference_request")
    assert receipt["provider"] == provider and receipt["status_code"] == 200
    assert receipt["estimated_cost_usd"] == pytest.approx(expected)
    if case == "fallback":
        assert [attempt["status"] for attempt in receipt["routing_attempts"]] == ["failed", "served"]
    if credential in caplog.text or credential in response.text:
        pytest.fail("Provider credential appeared in output", pytrace=False)
