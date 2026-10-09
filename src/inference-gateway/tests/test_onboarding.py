# ruff: noqa: F811
import asyncio
from dataclasses import replace

import pytest
from app.policy import ToolRoute
from app.runtime_client import RuntimeClient
from app.team_data import portable_settings
from cryptography.fernet import Fernet

from tests.test_teams import auth, team_gateway  # noqa: F401


def prepare(app, monkeypatch):
    monkeypatch.delenv("ONBOARDING_OPENAI_KEY", raising=False)
    app.state.settings = replace(app.state.settings, workflow_secrets_key=Fernet.generate_key().decode())
    team = app.state.sandbox_policy_set.policies["team"]
    policy = team.workflows["ResearchWorkflow"].model_copy(update={"allowed_tools": ["research", "publish"]})
    app.state.sandbox_policy_set.policies["team"] = replace(
        team,
        provider_credentials={"openai": "ONBOARDING_OPENAI_KEY"},
        workflows={"ResearchWorkflow": policy},
        tools={name: ToolRoute(url="http://fake/tool", costUsd=0) for name in ("research", "publish")},
    )


def test_onboarding_key_fix_selects_ready_model_without_exposing_secret(team_gateway, monkeypatch, caplog):
    client, app = team_gateway
    prepare(app, monkeypatch)
    path = "/v1/team/providers/openai/key"
    initial = client.get("/v1/team/onboarding", headers=auth("admin")).json()
    assert not initial["sample"]["ready"]
    assert initial["providers"][0]["can_save"]
    body = {"value": "private-provider-key", "expected_version": 0}
    response = client.put(path, headers=auth("admin"), json=body)
    assert response.status_code == 200, response.text
    assert "private-provider-key" not in response.text + caplog.text + str(app.state.budget_tracker.client.data)
    assert client.put(path, headers=auth("admin"), json=body).status_code == 409
    ready = client.get("/v1/team/onboarding", headers=auth("builder")).json()
    assert ready["sample"]["ready"]
    assert ready["sample"]["input"]["model"] == "primary"
    assert not ready["providers"][0]["can_save"]
    assert client.get("/v1/team", headers=auth("admin")).json()["provider_configuration"]["openai"]["configured"]
    exported = portable_settings(app.state.storage.get_settings("team"))
    assert "ciphertext" not in str(exported)
    runtime = RuntimeClient(app.state.settings)
    runtime.storage = app.state.storage
    runtime.sandbox_policies = app.state.sandbox_policy_set
    runtime.policy = app.state.model_routing_policy
    parts = asyncio.run(
        runtime._request_parts(
            {"model": "primary", "messages": []}, "openai", "chat/completions", {"X-Sandbox-ID": "team"}
        )
    )
    assert parts[2]["Authorization"] == "Bearer private-provider-key"
    assert client.get("/v1/team/onboarding", headers=auth("other")).json()["providers"] == []
    assert "private-provider-key" not in client.get("/v1/team/settings", headers=auth("admin")).text


@pytest.mark.parametrize("role", ["builder", "approver", "viewer", "project", "other"])
def test_provider_key_requires_unrestricted_admin(team_gateway, role, monkeypatch):
    client, app = team_gateway
    prepare(app, monkeypatch)
    assert (
        client.put(
            "/v1/team/providers/openai/key", headers=auth(role), json={"value": "secret", "expected_version": 0}
        ).status_code
        == 403
    )
    assert app.state.storage.get_settings("team") is None


def test_provider_provisioning_fails_closed_and_validates_input(team_gateway, monkeypatch):
    client, app = team_gateway
    prepare(app, monkeypatch)
    path = "/v1/team/providers/openai/key"
    assert client.put(path, headers=auth("admin"), json={"value": "bad\nkey", "expected_version": 0}).status_code == 422
    assert (
        client.put(
            "/v1/team/providers/vertex/key", headers=auth("admin"), json={"value": "secret", "expected_version": 0}
        ).status_code
        == 404
    )
    app.state.settings = replace(app.state.settings, workflow_secrets_key="")
    assert client.put(path, headers=auth("admin"), json={"value": "secret", "expected_version": 0}).status_code == 503
    assert app.state.storage.get_settings("team") is None
