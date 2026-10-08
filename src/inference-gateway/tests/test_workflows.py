import json
import logging
from dataclasses import replace
from fnmatch import fnmatchcase
from time import time
from uuid import uuid4

import httpx
import pytest
from app.budget import RedisSandboxBudgetTracker
from app.main import create_app
from app.policy import ModelRoute, ModelRoutingPolicy, SandboxPolicy, SandboxPolicySet, ToolRoute, WorkflowPolicy
from app.workflow_budget import INIT, RESERVE, SETTLE
from app.workflow_credentials import LEASE, RELEASE
from fastapi.testclient import TestClient

from tests.gateway_support import FakeRedisBudgetStore, _tool_settings


class RunRedis(FakeRedisBudgetStore):
    def __init__(self):
        super().__init__()
        self.now = time()
        self.expires = {}

    def get(self, key):
        if self.expires.get(key, float("inf")) <= self.now:
            self.delete(key)
        return self.data.get(key)

    def setex(self, key, seconds, value):
        assert seconds > 0
        self.data[key] = value
        self.expires[key] = self.now + seconds

    def hset(self, key, field, value):
        self.data.setdefault(key, {})[field] = value

    def hgetall(self, key):
        return dict(self.get(key) or {})

    def hget(self, key, field):
        return self.hgetall(key).get(field)

    def ttl(self, key):
        if self.get(key) is None:
            return -2
        return int(self.expires[key] - self.now) if key in self.expires else -1

    def expireat(self, key, deadline, *, lt=False):
        if self.get(key) is None or (lt and self.expires.get(key, float("inf")) <= deadline):
            return False
        self.expires[key] = deadline
        self.get(key)
        return True

    def scan(self, cursor, *, match, count):
        return 0, [key for key in list(self.data) if self.get(key) is not None and fnmatchcase(key, match)]

    def delete(self, key):
        self.expires.pop(key, None)
        return self.data.pop(key, None) is not None

    def eval(self, script, numkeys, key, *args):
        if script == LEASE:
            if key in self.data:
                return None
            self.data[key] = args[0]
            return "OK"
        if script == RELEASE:
            if self.data.get(key) == args[0]:
                return self.delete(key)
            return 0
        if script == INIT:
            tokens, cost, workflow, required = args
            old = self.data.get(key)
            if old:
                return int(
                    old["token_limit"] == tokens
                    and old["cost_limit"] == cost
                    and old["workflow"] == workflow
                    and old["policy_required"] == required
                )
            self.data[key] = {
                "token_limit": tokens,
                "cost_limit": cost,
                "tokens": 0,
                "cost": 0,
                "workflow": workflow,
                "policy_required": required,
            }
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
                "primary",
                "openai",
                fallbacks=("backup",),
                base_url="http://model.invalid",
                input_usd_per_1k_tokens=1,
                output_usd_per_1k_tokens=3,
            ),
            ModelRoute(
                "backup",
                "anthropic",
                base_url="http://backup.invalid",
                input_usd_per_1k_tokens=2,
                output_usd_per_1k_tokens=4,
            ),
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


def set_workflow_policy(client, **overrides):
    policy = WorkflowPolicy.model_validate(
        {
            "allowedProviders": ["openai", "anthropic"],
            "allowedModels": ["primary", "backup"],
            "allowedTools": ["publish"],
            "allowedEgress": ["http://model.invalid", "http://backup.invalid", "http://tool.invalid"],
            **overrides,
        }
    )
    team = client.app.state.sandbox_policy_set.policies["team"]
    client.app.state.sandbox_policy_set.policies["team"] = replace(team, workflows={"Briefing": policy})


def test_workflow_policy_discovery_binding_and_caps(gateway):
    client, _, run_id, _ = gateway
    set_workflow_policy(client, tokenLimit=200, costLimitUsd=0.5)
    assert client.get("/v1/workflow-policies").json()["workflows"]["Briefing"]["tokenLimit"] == 200
    assert client.get("/v1/workflow-policies", headers={"X-Sandbox-ID": "other"}).json() == {"workflows": {}}
    assert client.put(f"/v1/workflow-runs/{run_id}", json={}).status_code == 403
    response = client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing"})
    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "workflow": "Briefing", "token_limit": 200, "cost_limit_usd": 0.5}
    assert client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing"}).status_code == 200
    assert (
        client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing", "token_limit": 100}).status_code == 409
    )
    assert (
        client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing", "allowedModels": []}).status_code == 422
    )


@pytest.mark.parametrize(
    "restriction",
    [
        {"allowedProviders": ["anthropic"]},
        {"allowedModels": ["backup"]},
        {"allowedEgress": ["http://model.invalid.attacker"]},
    ],
)
def test_workflow_model_policy_denies_before_network(gateway, restriction, caplog):
    client, runtime, run_id, headers = gateway
    set_workflow_policy(client, **restriction)
    client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing"})
    with caplog.at_level(logging.INFO, logger="agentworkflows.audit"):
        response = chat(client, headers)
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "workflow_model_denied"
    assert runtime.calls == 0
    assert any('"workflow": "Briefing"' in record.message for record in caplog.records)


@pytest.mark.parametrize(
    "restriction",
    [
        {"allowedProviders": ["openai"]},
        {"allowedModels": ["primary"]},
        {"allowedEgress": ["http://model.invalid"]},
    ],
)
def test_workflow_fallback_cannot_escape_policy(gateway, restriction):
    client, runtime, run_id, headers = gateway
    set_workflow_policy(client, **restriction)
    client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing"})
    runtime.fail = True
    assert chat(client, headers).status_code == 502
    assert runtime.calls == 1


def test_messages_sdk_uses_run_policy_and_budget(gateway):
    client, runtime, run_id, headers = gateway
    set_workflow_policy(client)
    client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing"})
    body = {"model": "primary", "max_tokens": 20, "messages": [{"role": "user", "content": "hello"}]}
    assert client.post("/v1/messages", headers=headers, json=body).status_code == 200
    assert client.get(f"/v1/workflow-runs/{run_id}").json()["tokens"] == 7
    assert client.post("/v1/messages", headers=headers, json={**body, "stream": True}).status_code == 400
    set_workflow_policy(client, allowedProviders=[])
    assert client.post("/v1/messages", headers=headers, json=body).status_code == 403
    assert runtime.calls == 1


def test_mcp_team_registration_validates_and_does_not_expose_credentials(tmp_path):
    import yaml

    policy = {
        "apiVersion": "platform.ai/v1alpha1",
        "kind": "SandboxPolicySet",
        "spec": {
            "policies": [
                {
                    "sandboxId": "team",
                    "mcpServers": {
                        "docs": {
                            "url": "http://tools.invalid/mcp",
                            "credentialEnv": "MCP_TOKEN",
                            "tools": {"search": 0.01},
                        }
                    },
                    "workflows": {
                        "Briefing": {"allowedProviders": [], "allowedModels": [], "allowedTools": ["docs.search"]}
                    },
                }
            ]
        },
    }
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(policy))
    loaded = SandboxPolicySet.from_path(path).policies["team"]
    assert loaded.tools["docs.search"].mcp_tool == "search"
    policy["spec"]["policies"][0]["mcpServers"]["docs"]["tools"]["search"] = -1
    path.write_text(yaml.safe_dump(policy))
    with pytest.raises(ValueError):
        SandboxPolicySet.from_path(path)


@pytest.mark.parametrize("sse", [False, True])
def test_mcp_governance_handshake_dlp_receipts_and_session_cleanup(gateway, monkeypatch, caplog, sse):
    client, _, run_id, headers = gateway
    target = ToolRoute.model_validate(
        {"url": "http://tool.invalid/mcp", "costUsd": 0.1, "mcpTool": "search", "credentialEnv": "MCP_TOKEN"}
    )
    client.app.state.sandbox_policy_set.policies["team"].tools["docs.search"] = target
    set_workflow_policy(client, allowedTools=["docs.search"])
    client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing", "cost_limit_usd": 0.15})
    assert client.get("/v1/tools", headers=headers).json() == {"tools": ["docs.search"]}
    calls = []
    secret = "ghp_" + "Z" * 36
    monkeypatch.setenv("MCP_TOKEN", "server-credential")

    def respond(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer server-credential"
        assert secret not in request.content.decode()
        if request.method == "DELETE":
            return httpx.Response(200)
        body = json.loads(request.content)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}
        else:
            assert request.headers["Mcp-Session-Id"] == "test-session"
            assert request.headers["MCP-Protocol-Version"] == "2025-03-26"
            if body["method"] == "notifications/initialized":
                return httpx.Response(202)
            assert body["params"] == {"name": "search", "arguments": {"query": "approved"}}
            result = {"content": [{"type": "text", "text": secret}]}
        message = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        if sse:
            return httpx.Response(
                200,
                text=f"data: {json.dumps(message)}\r\n\r\n",
                headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": "test-session"},
            )
        return httpx.Response(200, json=message, headers={"Mcp-Session-Id": "test-session"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.workflow_api.httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw)
    )
    assert client.post("/v1/tools/publish/call", headers=headers, json={"arguments": {}}).status_code == 403
    assert (
        client.post(
            "/v1/tools/docs.search/call", headers=headers, json={"arguments": {"nested": [{"token": secret}]}}
        ).status_code
        == 400
    )
    assert not calls
    with caplog.at_level(logging.INFO, logger="agentworkflows.audit"):
        response = client.post("/v1/tools/docs.search/call", headers=headers, json={"arguments": {"query": "approved"}})
    assert response.status_code == 200, response.text
    assert secret not in response.text
    assert len(calls) == 4 and calls[-1].method == "DELETE"
    assert len({r.headers["Idempotency-Key"] for r in calls}) == 1
    assert client.post("/v1/tools/docs.search/call", headers=headers, json={"arguments": {}}).status_code == 403
    assert len(calls) == 4
    assert any('"backend": "mcp"' in record.message for record in caplog.records)
    assert all(secret not in record.message and "server-credential" not in record.message for record in caplog.records)


def test_container_credentials_are_scoped_revoked_and_workspace_is_exclusive(gateway, caplog):
    client, runtime, run_id, headers = gateway
    set_workflow_policy(
        client, agents={"coder": {"namespace": "team-code", "sandbox": "coder", "command": ["python", "/app/agent.py"]}}
    )
    client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing"})
    with caplog.at_level(logging.INFO, logger="agentworkflows.audit"):
        response = client.post("/v1/agents/coder/start", headers=headers, json={"arguments": {"task": "test"}})
    assert response.status_code == 200, response.text
    grant = response.json()
    key = {"Authorization": "Bearer " + grant["api_key"]}
    assert chat(client, key).status_code == 200
    assert client.post("/v1/agents/coder/start", headers=headers, json={"arguments": {}}).status_code == 409
    assert client.get("/v1/workflow-policies", headers=key).status_code == 403
    assert client.post("/v1/agents/coder/start", headers=key, json={"arguments": {}}).status_code == 403
    assert chat(client, {**key, **headers, "X-Workflow-Run-ID": str(uuid4())}).status_code == 403
    assert chat(client, {**key, "X-Sandbox-ID": "other"}).status_code == 403
    assert chat(client, {**key, **headers, "X-Workflow-Step-ID": "other"}).status_code == 403
    assert chat(client, {**key, **headers, "X-Workflow-Step-ID": "1/1"}).status_code == 200
    result = client.post(
        "/v1/agents/coder/finish",
        headers=headers,
        json={"credential_id": grant["credential_id"], "exit_code": 0, "output": "done"},
    )
    assert result.json() == {"result": "done"}
    assert chat(client, key).status_code == 401
    assert client.post("/v1/agents/coder/start", headers=headers, json={"arguments": {}}).status_code == 200
    assert runtime.calls == 2
    assert all(grant["api_key"] not in record.message for record in caplog.records)


def test_removing_a_workflow_policy_revokes_existing_run(gateway):
    client, runtime, run_id, headers = gateway
    set_workflow_policy(client)
    client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing"})
    client.app.state.sandbox_policy_set.policies.clear()
    assert chat(client, headers).status_code == 403
    assert client.get("/v1/tools", headers=headers).status_code == 403
    assert runtime.calls == 0


def test_workflow_tool_egress_denied_before_contact(gateway, monkeypatch):
    client, _, run_id, headers = gateway
    set_workflow_policy(client, allowedEgress=[])
    client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing"})
    response = client.post("/v1/tools/publish/call", headers=headers, json={"arguments": {}})
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "workflow_egress_denied"


@pytest.mark.parametrize("failure", ["redirect", "version", "rpc-error", "tool-error", "oversize", "wrong-id"])
def test_mcp_failures_are_bounded_receipted_and_remain_charged(gateway, monkeypatch, caplog, failure):
    client, _, run_id, headers = gateway
    client.app.state.sandbox_policy_set.policies["team"].tools["search"] = ToolRoute.model_validate(
        {"url": "http://tool.invalid/mcp", "costUsd": 0.1, "mcpTool": "search"}
    )
    client.put(f"/v1/workflow-runs/{run_id}", json={})
    calls = []

    def respond(request):
        calls.append(request)
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "http://unapproved.invalid"})
        if failure == "oversize":
            return httpx.Response(200, content=b"x" * (client.app.state.settings.max_request_body_bytes + 1))
        if request.method == "DELETE":
            return httpx.Response(200)
        body = json.loads(request.content)
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if failure == "rpc-error":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "error": {"code": -32603, "message": "private server detail"},
                },
            )
        result = (
            {"protocolVersion": "unknown" if failure == "version" else "2025-03-26", "capabilities": {"tools": {}}}
            if body["method"] == "initialize"
            else {"isError": True, "content": []}
        )
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 999 if failure == "wrong-id" else body["id"], "result": result},
            headers={"Mcp-Session-Id": "test-session"},
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.workflow_api.httpx.AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw)
    )
    with caplog.at_level(logging.INFO, logger="agentworkflows.audit"):
        response = client.post("/v1/tools/search/call", headers=headers, json={"arguments": {}})
    assert response.status_code == 502
    assert "private server detail" not in response.text
    assert all(request.url.host == "tool.invalid" for request in calls)
    assert client.get(f"/v1/workflow-runs/{run_id}").json()["cost_usd"] == 0.1
    assert any('"status_code": 502' in record.message for record in caplog.records)
    if failure in {"version", "tool-error", "wrong-id"}:
        assert calls[-1].method == "DELETE"


def test_expired_container_credential_fails_closed(gateway):
    client, runtime, run_id, headers = gateway
    set_workflow_policy(client, agents={"coder": {"namespace": "team-code", "sandbox": "coder", "command": ["true"]}})
    client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing"})
    grant = client.post("/v1/agents/coder/start", headers=headers, json={"arguments": {}}).json()
    store = client.app.state.budget_tracker.client
    for key in list(store.data):
        if ":step-credential:" in key:
            store.delete(key)
    response = chat(client, {"Authorization": "Bearer " + grant["api_key"]})
    assert response.status_code == 401
    assert response.json()["detail"]["reason"] == "step_credential_expired"
    assert runtime.calls == 0
