import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.main import create_app
from app.settings import Settings
from app.workflow_operations import temporal_client
from fastapi.testclient import TestClient

from tests.gateway_support import FakeRuntimeClient, _tool_settings
from tests.test_cloud_providers import reply, streamed_reply


@pytest.mark.parametrize("healthy", [True, False])
def test_temporal_readiness_and_independent_liveness(healthy):
    app = create_app(_tool_settings(temporal_address="temporal:7233"))
    app.state.runtime_client = FakeRuntimeClient()
    app.state.temporal_client = SimpleNamespace(
        service_client=SimpleNamespace(check_health=AsyncMock(return_value=healthy))
    )
    with TestClient(app) as client:
        response = client.get("/readyz")
        assert response.status_code == (200 if healthy else 503)
        assert response.json()["dependencies"]["temporal"]["status"] == ("ok" if healthy else "unavailable")
        assert client.get("/healthz").status_code == 200


def test_temporal_readiness_is_bounded_and_redacts_failures():
    app = create_app(_tool_settings(temporal_address="temporal:7233"))
    app.state.runtime_client = FakeRuntimeClient()

    async def hung():
        await asyncio.sleep(60)

    app.state.temporal_client = SimpleNamespace(service_client=SimpleNamespace(check_health=hung))
    with TestClient(app) as client:
        response = client.get("/readyz")
        assert response.status_code == 503 and "temporal:7233" not in response.text


@pytest.mark.parametrize("address", [None, "", "temporal:7233"])
def test_temporal_address_settings_and_connection(monkeypatch, address):
    monkeypatch.delenv("TEMPORAL_ADDRESS", raising=False)
    monkeypatch.delenv("TEMPORAL_NAMESPACE", raising=False)
    if address is not None:
        monkeypatch.setenv("TEMPORAL_ADDRESS", address)
    settings = Settings.from_env()
    assert settings.temporal_address == (address or "")
    app = create_app(settings)
    monkeypatch.setenv("TEMPORAL_ADDRESS", "changed-after-startup:7233")
    connect = AsyncMock()
    monkeypatch.setattr("app.workflow_operations.Client.connect", connect)

    asyncio.run(temporal_client(app))

    connect.assert_awaited_once_with(
        address or "temporal-frontend.workflows.svc.cluster.local:7233", namespace="default"
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic", "azure-openai", "bedrock", "vertex"])
@pytest.mark.parametrize("case", ["chat", "streaming", "tools", "fallback"])
def test_live_acceptance_assertions_with_protocol_fixtures(monkeypatch, caplog, provider, case):
    from tests.live.test_providers import PROVIDERS, test_live_provider

    prefix = "LIVE_" + provider.upper().replace("-", "_")
    monkeypatch.setenv(PROVIDERS[provider][0], "fixture-only-never-a-real-key")
    for suffix, value in {
        "MODEL": "fixture-model",
        "BASE_URL": "https://fake.invalid/v1",
        "INPUT_USD_PER_1K": "1",
        "OUTPUT_USD_PER_1K": "3",
    }.items():
        monkeypatch.setenv(f"{prefix}_{suffix}", value)

    def respond(request):
        if case == "streaming":
            return httpx.Response(200, content=streamed_reply(provider))
        import json

        payload = json.dumps(reply(provider, tool=case == "tools"))
        payload = payload.replace("lookup", "echo").replace('\\"q\\"', '\\"text\\"').replace('"q"', '"text"')
        return httpx.Response(200, content=payload)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(**{**kwargs, "transport": httpx.MockTransport(respond)})
    )
    test_live_provider(provider, case, caplog, monkeypatch)
