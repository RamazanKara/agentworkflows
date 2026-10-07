import json
import logging
from uuid import uuid4

import httpx
import pytest
from app.budget import RedisSandboxBudgetTracker
from app.main import create_app
from app.policy import ModelRoute, ModelRoutingPolicy, SandboxPolicy, SandboxPolicySet, ToolRoute
from app.workflow_budget import INIT, RESERVE, SETTLE
from fastapi.testclient import TestClient

from tests.gateway_support import FakeRedisBudgetStore, _tool_settings


class RunRedis(FakeRedisBudgetStore):
    def eval(self, script, numkeys, key, *args):
        if script == INIT:
            tokens, cost = args
            old = self.data.get(key)
            if old:
                return int(old["token_limit"] == tokens and old["cost_limit"] == cost)
            self.data[key] = {"token_limit": tokens, "cost_limit": cost, "tokens": 0, "cost": 0}
            return 1
        if script == RESERVE:
            if key not in self.data:
                return -1
            record = self.data[key]
            if record["tokens"] + args[0] > record["token_limit"]:
                return 1
            if record["cost"] + args[1] > record["cost_limit"]:
                return 2
            record["tokens"] += args[0]
            record["cost"] += args[1]
            return 0
        if script == SETTLE:
            self.data[key]["tokens"] += args[0]
            self.data[key]["cost"] += args[1]
            return 1
        return super().eval(script, numkeys, key, *args)


@pytest.fixture
def gateway():
    settings = _tool_settings(
        sandbox_budget_backend="redis",
        allowed_models=("primary", "backup"),
        output_guardrail_enabled=True,
        output_guardrail_mode="redact",
    )
    app = create_app(settings)
    app.state.budget_tracker = RedisSandboxBudgetTracker(settings, client=RunRedis())
    app.state.model_routing_policy = ModelRoutingPolicy(
        (
            ModelRoute(
                "primary", "openai", fallbacks=("backup",), input_usd_per_1k_tokens=1, output_usd_per_1k_tokens=3
            ),
            ModelRoute("backup", "anthropic", input_usd_per_1k_tokens=2, output_usd_per_1k_tokens=4),
        )
    )
    app.state.sandbox_policy_set = SandboxPolicySet(
        {
            "team": SandboxPolicy(
                "team",
                tools={"publish": ToolRoute.model_validate({"url": "http://tool.invalid/publish", "costUsd": 0.1})},
            )
        }
    )

    class Runtime:
        calls = 0
        fail = False

        async def chat_completions(self, payload, headers=None, backend=None, retry=True):
            assert retry is False
            self.calls += 1
            if self.fail and backend == "openai":
                raise httpx.ConnectError("fixture failure")
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            }

        async def aclose(self):
            pass

    runtime = Runtime()
    app.state.runtime_client = runtime
    client = TestClient(app, headers={"X-Sandbox-ID": "team"})
    run_id = str(uuid4())
    headers = {"X-Workflow-Run-ID": run_id, "X-Workflow-Step-ID": "1"}
    return client, runtime, run_id, headers


def chat(client, headers):
    return client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "primary", "max_tokens": 20, "messages": [{"role": "user", "content": "hello"}]},
    )


def test_run_budget_is_immutable_and_team_scoped(gateway):
    client, _, run_id, _ = gateway
    assert client.put(f"/v1/workflow-runs/{run_id}", json={}).status_code == 200
    assert client.put(f"/v1/workflow-runs/{run_id}", json={}).status_code == 200
    assert client.put(f"/v1/workflow-runs/{run_id}", json={"token_limit": 20000}).status_code == 409
    assert client.get(f"/v1/workflow-runs/{run_id}", headers={"X-Sandbox-ID": "other"}).status_code == 404


@pytest.mark.parametrize(
    "limits,reason",
    [
        ({"token_limit": 1}, "workflow_token_budget_exceeded"),
        ({"cost_limit_usd": 0.001}, "workflow_cost_budget_exceeded"),
    ],
)
def test_budget_denied_before_provider(gateway, limits, reason):
    client, runtime, run_id, headers = gateway
    client.put(f"/v1/workflow-runs/{run_id}", json=limits)
    response = chat(client, headers)
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == reason
    assert runtime.calls == 0


def test_fallback_is_charged_and_receipts_link_run_and_step(gateway, caplog):
    client, runtime, run_id, headers = gateway
    runtime.fail = True
    client.put(f"/v1/workflow-runs/{run_id}", json={})
    with caplog.at_level(logging.INFO, logger="agentworkflows.audit"):
        assert chat(client, headers).status_code == 200
    budget = client.get(f"/v1/workflow-runs/{run_id}").json()
    assert budget["tokens"] == 22 + 7
    assert budget["cost_usd"] == pytest.approx(0.066 + 0.018)
    event = next(json.loads(record.message) for record in caplog.records if record.name == "agentworkflows.audit")
    assert event["workflow_run_id"] == run_id
    assert event["workflow_step_id"] == "1"
    assert [attempt["status"] for attempt in event["routing_attempts"]] == ["failed", "served"]
    assert runtime.calls == 2


def test_run_budget_survives_a_new_gateway_tracker(gateway):
    client, _, run_id, headers = gateway
    client.put(f"/v1/workflow-runs/{run_id}", json={"token_limit": 28})
    assert chat(client, headers).status_code == 200
    old = client.app.state.budget_tracker
    client.app.state.budget_tracker = RedisSandboxBudgetTracker(old.settings, client=old.client)
    assert chat(client, headers).status_code == 403


def test_workflow_context_and_streaming_fail_closed(gateway):
    client, runtime, run_id, headers = gateway
    assert chat(client, headers).json()["detail"]["reason"] == "workflow_run_missing"
    assert chat(client, {**headers, "X-Workflow-Run-ID": "bad"}).status_code == 400
    client.put(f"/v1/workflow-runs/{run_id}", json={})
    response = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "primary", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.json()["detail"]["reason"] == "workflow_streaming_unsupported"
    assert client.get("/v1/models", headers=headers).status_code == 400
    assert runtime.calls == 0


def test_tool_policy_dlp_cost_and_idempotency(gateway, monkeypatch, caplog):
    client, _, run_id, headers = gateway
    client.put(f"/v1/workflow-runs/{run_id}", json={"cost_limit_usd": 0.15})
    assert client.get("/v1/tools").json() == {"tools": ["publish"]}
    assert client.post("/v1/tools/unknown/call", headers=headers, json={"arguments": {}}).status_code == 403
    secret = "ghp_" + "Z" * 36
    assert (
        client.post("/v1/tools/publish/call", headers=headers, json={"arguments": {"body": secret}}).status_code == 400
    )
    calls = []

    def tool(request):
        calls.append(request)
        return httpx.Response(200, json={"body": secret})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.workflow_api.httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(tool), **kw)
    )
    with caplog.at_level(logging.INFO, logger="agentworkflows.audit"):
        response = client.post("/v1/tools/publish/call", headers=headers, json={"arguments": {"body": "reviewed"}})
    assert response.status_code == 200, response.text
    assert secret not in response.text
    assert len(calls[0].headers["Idempotency-Key"]) == 64
    assert client.post("/v1/tools/publish/call", headers=headers, json={"arguments": {}}).status_code == 403
    assert len(calls) == 1
    assert any('"action_type": "tool_exec"' in record.message for record in caplog.records)


def test_workflows_require_durable_budget_backend():
    client = TestClient(create_app(_tool_settings()))
    response = client.put(f"/v1/workflow-runs/{uuid4()}", json={})
    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "workflow_store_required"
