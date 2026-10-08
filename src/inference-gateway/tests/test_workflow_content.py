# ruff: noqa: F811  (shared gateway fixtures are imported, then requested by name)
import json
import logging
from dataclasses import replace

import httpx
import pytest
from app.policy import SandboxPolicySet, WorkflowPolicy
from app.settings import Settings
from app.workflow_content import content_key

from tests.test_teams import auth, start, team_gateway  # noqa: F401
from tests.test_workflows import gateway, set_workflow_policy  # noqa: F401


@pytest.mark.parametrize("mode", ["none", "redacted", "full"])
@pytest.mark.parametrize("kind", ["model", "tool"])
def test_step_capture_modes_are_separate_from_receipts(gateway, monkeypatch, caplog, mode, kind):
    client, runtime, run_id, headers = gateway
    settings = client.app.state.settings
    # Flagging permits the original values upstream; redacted capture still removes them.
    object.__setattr__(settings, "prompt_secret_mode", "flag")
    object.__setattr__(settings, "output_guardrail_mode", "flag")
    set_workflow_policy(client, captureContent=mode)
    assert client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing"}).status_code == 200
    secret = "ghp_" + "a" * 36

    async def chat(*args, **kwargs):
        return {"choices": [{"message": {"content": secret}}]}

    monkeypatch.setattr(runtime, "chat_completions", chat)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.workflow_api.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=secret))),
    )
    with caplog.at_level(logging.INFO, logger="agentworkflows.audit"):
        if kind == "model":
            response = client.post(
                "/v1/chat/completions", headers=headers,
                json={"model": "primary", "max_tokens": 1, "messages": [{"role": "user", "content": secret}]},
            )
        else:
            response = client.post("/v1/tools/publish/call", headers=headers, json={"arguments": {"body": secret}})
    assert response.status_code == 200, response.text
    assert secret not in caplog.text
    store = client.app.state.budget_tracker.client
    base = f"{settings.sandbox_budget_key_prefix}:workflow:team:{run_id}"
    raw = store.get(content_key(base, "1"))
    if mode == "none":
        assert raw is None
        assert base + ":capture" not in store.data
    else:
        content = json.loads(raw)
        assert content["redaction"] == mode
        assert content["truncated"] == {"input": False, "output": False}
        assert (secret in content["input"]) is (mode == "full")
        assert (secret in content["output"]) is (mode == "full")
        if mode == "redacted":
            assert "[REDACTED:" in content["input"] and "[REDACTED:" in content["output"]
        assert store.ttl(content_key(base, "1")) == settings.content_retention_seconds


def test_capture_truncates_utf8_after_redaction_and_preserves_dlp(gateway, monkeypatch):
    client, runtime, run_id, headers = gateway
    settings = client.app.state.settings
    object.__setattr__(settings, "content_max_bytes", 17)
    object.__setattr__(settings, "content_retention_seconds", 42)
    object.__setattr__(settings, "prompt_secret_mode", "redact")
    set_workflow_policy(client, captureContent="full")
    client.put(f"/v1/workflow-runs/{run_id}", json={"workflow": "Briefing"})
    secret = "ghp_" + "b" * 36

    async def chat(payload, **kwargs):
        assert secret not in json.dumps(payload)
        return {"choices": [{"message": {"content": "é" * 20 + " " + secret}}]}

    monkeypatch.setattr(runtime, "chat_completions", chat)
    response = client.post(
        "/v1/chat/completions", headers=headers,
        json={"model": "primary", "max_tokens": 1, "messages": [{"role": "user", "content": secret}]},
    )
    assert response.status_code == 200, response.text
    assert secret not in response.text
    base = f"{settings.sandbox_budget_key_prefix}:workflow:team:{run_id}"
    store = client.app.state.budget_tracker.client
    content = json.loads(store.get(content_key(base, "1")))
    assert content["truncated"] == {"input": True, "output": True}
    assert len(content["input"].encode()) <= 17
    assert content["output"] == "é" * 8
    assert store.ttl(content_key(base, "1")) == 42


@pytest.mark.parametrize("role", ["admin", "builder", "approver", "viewer"])
def test_run_content_visibility_and_expiry(team_gateway, role):
    client, app = team_gateway
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(team, capture_content="redacted")
    run = start(client).json()
    path = f"/v1/workflow-runs/{run['run_id']}"
    worker = {**auth("worker"), "X-Workflow-ID": run["workflow_id"]}
    assert client.put(path, headers=worker, json={"workflow": "ResearchWorkflow"}).status_code == 200
    headers = {**auth("worker"), "X-Workflow-Run-ID": run["run_id"], "X-Workflow-Step-ID": "model"}
    response = client.post(
        "/v1/chat/completions", headers=headers,
        json={"model": "primary", "max_tokens": 5, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200, response.text
    detail = client.get(path, headers=auth(role)).json()
    step = detail["timeline"][0]
    assert step["content"]["output"] == "ok"
    assert "content" not in step["receipt"]
    assert "hello" not in json.dumps(step["receipt"])
    for forbidden in ("other", "project"):
        response = client.get(path, headers=auth(forbidden))
        assert response.status_code == 404
        assert "hello" not in response.text
    assert client.get(path).status_code == 401
    store = app.state.budget_tracker.client
    store.now += app.state.settings.content_retention_seconds + 1
    step = client.get(path, headers=auth(role)).json()["timeline"][0]
    assert step["content"] is None and step["content_reason"] == "expired"


def test_capture_off_reason_and_team_default_overrides(team_gateway, tmp_path):
    client, app = team_gateway
    run = start(client).json()
    assert client.post(f"/v1/workflow-runs/{run['run_id']}/cancel", headers=auth("builder")).status_code == 200
    step = client.get(f"/v1/workflow-runs/{run['run_id']}", headers=auth("viewer")).json()["timeline"][0]
    assert step["content"] is None and step["content_reason"] == "capture_off"
    policy = {"allowedProviders": [], "allowedModels": []}
    manifest = {"apiVersion": "platform.ai/v1alpha1", "kind": "SandboxPolicySet", "spec": {"policies": [{
        "sandboxId": "team", "captureContent": "redacted",
        "workflows": {"Inherited": policy, "Disabled": {**policy, "captureContent": "none"}},
    }]}}
    path = tmp_path / "policies.json"
    path.write_text(json.dumps(manifest))
    app.state.sandbox_policy_set = SandboxPolicySet.from_path(path)
    policies = client.get("/v1/workflow-policies", headers=auth("admin")).json()["workflows"]
    assert policies["Inherited"]["captureContent"] == "redacted"
    assert policies["Disabled"]["captureContent"] == "none"
    assert WorkflowPolicy.model_validate(policy).capture_content == "none"


@pytest.mark.parametrize("name, default", [
    ("CONTENT_RETENTION_SECONDS", 604800), ("CONTENT_MAX_BYTES", 16384),
    ("RUN_RECORD_RETENTION_SECONDS", 2592000),
])
def test_retention_settings_defaults_and_environment(monkeypatch, name, default):
    monkeypatch.delenv(name, raising=False)
    assert getattr(Settings.from_env(), name.lower()) == default
    monkeypatch.setenv(name, "42")
    assert getattr(Settings.from_env(), name.lower()) == 42
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError):
        Settings.from_env()
