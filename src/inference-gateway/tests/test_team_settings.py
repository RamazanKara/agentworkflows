# ruff: noqa: F811
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from app.budget import RedisSandboxBudgetTracker
from app.policy import SandboxPolicySet
from app.team_settings import CHANGE, layer_settings
from redis.exceptions import ConnectionError

from tests.test_audit_verify import _double_logged_lines, _load_verifier
from tests.test_teams import auth, start, team_gateway  # noqa: F401

PATH = "/v1/team/settings"
WORKFLOW = "workflows.ResearchWorkflow."


@pytest.fixture
def settings_gateway(team_gateway):
    client, app = team_gateway
    primary, backup = app.state.model_routing_policy.routes
    app.state.model_routing_policy = replace(
        app.state.model_routing_policy, routes=(replace(primary, aliases=("research",)), backup),
    )
    return client, app


def update(client, fields, revision=0):
    return client.patch(PATH, headers={**auth("admin"), "If-Match": str(revision)}, json={"fields": fields})


def chat(client, headers=None, model="research"):
    return client.post(
        "/v1/chat/completions", headers=headers or auth("builder"),
        json={"model": model, "max_tokens": 20, "messages": [{"role": "user", "content": "hello"}]},
    )


def test_layering_defaults_overrides_and_team_isolation(settings_gateway):
    client, app = settings_gateway
    original = app.state.sandbox_policy_set.policies["team"]
    initial = client.get(PATH, headers=auth("admin"))
    assert initial.headers["etag"] == '"0"'
    assert initial.json()["fields"]["cost_limit_usd"] == {"value": 1, "source": "policy", "policy_default": 1}
    changes = {"cost_limit_usd": 20, "project_budgets.default": 10, WORKFLOW + "token_limit": 200,
               WORKFLOW + "allowed_providers": ["anthropic"], "model_routes.research": "backup"}
    saved = update(client, changes)
    assert saved.status_code == 200, saved.text
    assert saved.headers["etag"] == '"1"'
    data = saved.json()
    assert data["revision"] == 1 and data["updated_by"] == "admin" and data["updated_at"] > 0
    assert all(data["fields"][field]["value"] == value for field, value in changes.items())
    assert all(data["fields"][field]["source"] == "override" for field in changes)
    assert data["fields"][WORKFLOW + "cost_limit_usd"]["source"] == "policy"
    team = client.get("/v1/team", headers=auth("viewer")).json()
    assert team["cost_limit_usd"] == 20 and team["project_budgets"]["default"] == 10
    assert team["model_routes"] == {"research": "backup"}
    workflow = client.get("/v1/workflow-policies", headers=auth("viewer")).json()["workflows"]["ResearchWorkflow"]
    assert workflow["tokenLimit"] == 200 and workflow["allowedProviders"] == ["anthropic"]
    assert original.cost_limit_usd == 1 and original.workflows["ResearchWorkflow"].token_limit == 10000
    assert client.get("/v1/team", headers=auth("other")).json()["cost_limit_usd"] is None
    assert set(client.get("/v1/team", headers=auth("project")).json()["project_budgets"]) == {"private"}
    app.state.budget_tracker = RedisSandboxBudgetTracker(app.state.settings, client=app.state.budget_tracker.client)
    assert client.get(PATH, headers=auth("admin")).json()["revision"] == 1


def test_reset_one_field_uses_current_yaml_default_and_preserves_other_overrides(settings_gateway):
    client, app = settings_gateway
    assert update(client, {"cost_limit_usd": 20, "project_budgets.default": 10}).status_code == 200
    app.state.sandbox_policy_set.policies["team"] = replace(
        app.state.sandbox_policy_set.policies["team"], cost_limit_usd=3,
    )
    response = client.delete(PATH + "/cost_limit_usd", headers={**auth("admin"), "If-Match": '"1"'})
    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert response.json()["fields"]["cost_limit_usd"] == {"value": 3, "source": "policy", "policy_default": 3}
    assert response.json()["fields"]["project_budgets.default"]["source"] == "override"
    assert client.delete(PATH + "/cost_limit_usd", headers={**auth("admin"), "If-Match": "1"}).status_code == 409


def test_revision_conflicts_do_not_clobber_or_emit_receipts(settings_gateway, caplog):
    client, _ = settings_gateway
    caplog.set_level(logging.INFO)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda amount: update(client, {"cost_limit_usd": amount}), [5, 10]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    stored = client.get(PATH, headers=auth("admin")).json()
    assert stored["revision"] == 1 and stored["fields"]["cost_limit_usd"]["value"] in {5, 10}
    verifier = _load_verifier()
    events = verifier.deduplicate(verifier.extract_audit_events(_double_logged_lines(caplog)))
    assert len(events) == 1 and events[0]["event"] == "team_settings_changed"


def test_compare_and_set_checks_revision_inside_redis(settings_gateway, monkeypatch):
    client, app = settings_gateway
    store = app.state.budget_tracker.client
    original = store.eval

    def race(script, numkeys, key, *args):
        if script == CHANGE:
            store.data[key] = json.dumps({"revision": 1, "updated_by": "other-admin", "updated_at": 1,
                                          "overrides": {"cost_limit_usd": 7}})
        return original(script, numkeys, key, *args)

    monkeypatch.setattr(store, "eval", race)
    assert update(client, {"cost_limit_usd": 12}).status_code == 409
    assert client.get(PATH, headers=auth("admin")).json()["fields"]["cost_limit_usd"]["value"] == 7


@pytest.mark.parametrize("role", ["builder", "approver", "viewer", "worker", "other"])
def test_settings_require_admin_for_all_methods(settings_gateway, role):
    client, _ = settings_gateway
    for method, path in (("GET", PATH), ("PATCH", PATH), ("DELETE", PATH + "/cost_limit_usd")):
        response = client.request(method, path, headers=auth(role))
        assert response.status_code == 403
        assert response.json()["detail"]["reason"] == "team_role_required"


def test_project_bound_admin_cannot_edit_team_settings(settings_gateway):
    client, app = settings_gateway
    app.state.key_record_set = replace(app.state.key_record_set, records=tuple(
        replace(record, project="private") if record.key_id == "admin" else record
        for record in app.state.key_record_set.records
    ))
    assert update(client, {"cost_limit_usd": 20}).status_code == 403


@pytest.mark.parametrize(("field", "value"), [
    ("cost_limit_usd", -1), ("cost_limit_usd", True), ("cost_limit_usd", "12"), ("cost_limit_usd", None),
    ("project_budgets.unknown", 1), ("project_budgets.default", -1),
    (WORKFLOW + "token_limit", 1.5), (WORKFLOW + "token_limit", True), (WORKFLOW + "token_limit", -1),
    (WORKFLOW + "cost_limit_usd", -1), (WORKFLOW + "approval_threshold_usd", -1),
    (WORKFLOW + "approval_required", "true"), (WORKFLOW + "approver_role", "owner"),
    (WORKFLOW + "allowed_providers", ["unknown"]), (WORKFLOW + "allowed_providers", "openai"),
    ("model_routes.research", "unknown"), ("model_routes.unknown", "backup"),
    ("provider_credentials", {"openai": "secret"}), ("connection.credential_env", "secret"),
])
def test_validation_is_field_specific_and_atomic(settings_gateway, field, value):
    client, _ = settings_gateway
    response = update(client, {"project_budgets.private": 2, field: value})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["fields"][0]["field"] == field
    data = client.get(PATH, headers=auth("admin")).json()
    assert data["revision"] == 0 and data["fields"]["project_budgets.private"]["source"] == "policy"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1" + "0" * 400])
def test_non_finite_and_oversized_budgets_are_422(settings_gateway, value):
    client, _ = settings_gateway
    response = client.patch(PATH, headers={**auth("admin"), "If-Match": "0", "Content-Type": "application/json"},
                            content='{"fields":{"cost_limit_usd":' + value + '}}')
    assert response.status_code == 422


@pytest.mark.parametrize("revision", [None, "*", 'W/"0"', "-1", "1.5", "invalid"])
def test_revision_is_required_and_well_formed(settings_gateway, revision):
    client, _ = settings_gateway
    headers = {**auth("admin"), **({"If-Match": revision} if revision is not None else {})}
    assert client.patch(PATH, headers=headers, json={"fields": {"cost_limit_usd": 2}}).status_code == 422


def test_changes_are_chained_with_before_after_and_actor_without_credentials(settings_gateway, caplog, monkeypatch):
    client, app = settings_gateway
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret-never-store")
    assert update(client, {"cost_limit_usd": 2}).status_code == 200
    assert client.delete(PATH + "/cost_limit_usd", headers={**auth("admin"), "If-Match": "1"}).status_code == 200
    verifier = _load_verifier()
    events = verifier.deduplicate(verifier.extract_audit_events(_double_logged_lines(caplog)))
    assert len(events) == 2
    assert verifier.verify_chain(verifier.group_into_chains(events)[0]).ok
    assert events[0]["actor"] == "admin" and events[0]["revision"] == 1
    assert events[0]["before"]["cost_limit_usd"]["value"] == 1
    assert events[0]["after"]["cost_limit_usd"]["value"] == 2
    assert events[1]["after"]["cost_limit_usd"]["source"] == "policy"
    assert "provider-secret-never-store" not in json.dumps(app.state.budget_tracker.client.data)
    assert "provider-secret-never-store" not in caplog.text


@pytest.mark.parametrize("field", ["cost_limit_usd", "project_budgets.default"])
def test_monthly_budget_enforcement_changes_without_restart(settings_gateway, field):
    client, app = settings_gateway
    assert chat(client).status_code == 200
    assert update(client, {field: 0}).status_code == 200
    denied = chat(client)
    assert denied.status_code == (403 if field == "cost_limit_usd" else 400)
    expected = "team" if field == "cost_limit_usd" else "project"
    assert denied.json()["detail"]["reason"] == f"{expected}_cost_budget_exceeded"
    assert app.state.runtime_client.calls == 1
    assert update(client, {field: 1}, revision=1).status_code == 200
    assert chat(client).status_code == 200
    report = client.get("/v1/usage", headers=auth("viewer")).json()["spend"]
    assert report["reserved_and_spent_usd"] == 0.01
    assert report["project_budgets"]["default"]["reserved_and_spent_usd"] == 0.01
    assert datetime.fromtimestamp(report["window_start"], UTC).day == 1
    assert report["period"] == "month"


def initialized_run(client):
    run = start(client).json()
    path = f"/v1/workflow-runs/{run['run_id']}"
    headers = {**auth("worker"), "X-Workflow-ID": run["workflow_id"]}
    assert client.put(path, headers=headers, json={"workflow": "ResearchWorkflow"}).status_code == 200
    return run, path, headers


@pytest.mark.parametrize(("field", "reason"), [
    ("cost_limit_usd", "workflow_cost_budget_exceeded"), ("token_limit", "workflow_token_budget_exceeded"),
])
def test_active_run_honors_live_caps_and_initialization_stays_idempotent(settings_gateway, field, reason):
    client, app = settings_gateway
    run, path, headers = initialized_run(client)
    assert update(client, {WORKFLOW + field: 0}).status_code == 200
    assert client.put(path, headers=headers, json={"workflow": "ResearchWorkflow"}).status_code == 200
    step = {**auth("worker"), "X-Workflow-Run-ID": run["run_id"], "X-Workflow-Step-ID": "draft"}
    response = chat(client, step)
    assert response.status_code == 403 and response.json()["detail"]["reason"] == reason
    assert app.state.runtime_client.calls == 0
    assert client.get("/v1/usage", headers=auth("viewer")).json()["spend"]["reserved_and_spent_usd"] == 0
    assert client.get(path, headers=auth("viewer")).json()["budget"][field] == 0
    assert update(client, {WORKFLOW + field: 500}, revision=1).status_code == 200
    assert chat(client, step).status_code == 200


def test_routing_and_workflow_provider_admission_change_without_restart(settings_gateway):
    client, app = settings_gateway
    runtime = app.state.runtime_client.chat_completions
    calls = []

    async def record(payload, **kwargs):
        calls.append(payload["model"])
        return await runtime(payload, **kwargs)

    app.state.runtime_client.chat_completions = record
    run, _, _ = initialized_run(client)
    headers = {**auth("worker"), "X-Workflow-Run-ID": run["run_id"], "X-Workflow-Step-ID": "draft"}
    assert chat(client, headers).status_code == 200
    response = update(client, {"model_routes.research": "backup", WORKFLOW + "allowed_providers": ["openai"]})
    assert response.status_code == 200
    assert chat(client, headers).json()["detail"]["reason"] == "workflow_model_denied"
    assert update(client, {WORKFLOW + "allowed_providers": ["anthropic"]}, revision=1).status_code == 200
    assert chat(client, headers).status_code == 200
    assert calls == ["primary", "backup"]


def test_pre_override_run_budgets_keep_their_caps_and_can_retry_initialization(settings_gateway):
    client, app = settings_gateway
    _, path, headers = initialized_run(client)
    store = app.state.budget_tracker.client
    raw = next(value for value in store.data.values() if isinstance(value, dict) and "token_limit" in value)
    raw.pop("requested_limits")
    raw.update(token_limit=200, cost_limit=500_000_000)
    assert update(client, {WORKFLOW + "token_limit": 500, WORKFLOW + "cost_limit_usd": 10}).status_code == 200
    response = client.put(path, headers=headers, json={"workflow": "ResearchWorkflow"})
    assert response.status_code == 200
    assert response.json()["token_limit"] == 200 and response.json()["cost_limit_usd"] == 0.5
    assert raw["token_limit"] == 200 and raw["cost_limit"] == 500_000_000


def test_single_route_uses_alias_override(settings_gateway):
    client, app = settings_gateway
    app.state.runtime_client.embeddings = AsyncMock(return_value={"data": [], "usage": {"total_tokens": 1}})
    assert update(client, {"model_routes.research": "backup"}).status_code == 200
    response = client.post("/v1/embeddings", headers=auth("builder"), json={"model": "research", "input": "hi"})
    assert response.status_code == 200, response.text
    assert app.state.runtime_client.embeddings.call_args.args[0]["model"] == "backup"


def test_fallback_rechecks_provider_settings_after_a_slow_attempt(settings_gateway):
    client, app = settings_gateway
    primary, backup = app.state.model_routing_policy.routes
    app.state.model_routing_policy = replace(
        app.state.model_routing_policy, routes=(replace(primary, fallbacks=("backup",)), backup),
    )
    run, _, _ = initialized_run(client)
    calls = []

    async def fail_after_change(payload, **kwargs):
        calls.append(payload["model"])
        assert update(client, {WORKFLOW + "allowed_providers": ["openai"]}).status_code == 200
        raise httpx.ConnectError("provider unavailable")

    app.state.runtime_client.chat_completions = fail_after_change
    headers = {**auth("worker"), "X-Workflow-Run-ID": run["run_id"], "X-Workflow-Step-ID": "draft"}
    response = chat(client, headers)
    assert response.status_code == 403 and response.json()["detail"]["reason"] == "workflow_model_denied"
    assert calls == ["primary"]


def test_approval_role_is_effective_for_existing_runs(settings_gateway):
    client, _ = settings_gateway
    _, path, _ = initialized_run(client)
    assert update(client, {WORKFLOW + "approver_role": "admin"}).status_code == 200
    response = client.post(path + "/approve", headers=auth("approver"), json={})
    assert response.status_code == 403 and response.json()["detail"]["reason"] == "team_role_required"
    assert client.post(path + "/approve", headers=auth("admin"), json={}).status_code == 200


@pytest.mark.parametrize(("changes", "automatic"), [
    ({"approval_required": False}, True), ({"approval_threshold_usd": 1}, True),
    ({"approval_required": True, "approval_threshold_usd": 0}, False),
])
def test_approval_requirement_and_threshold_apply_at_existing_review_gate(settings_gateway, changes, automatic):
    client, app = settings_gateway
    run, path, _ = initialized_run(client)
    assert update(client, {WORKFLOW + key: value for key, value in changes.items()}).status_code == 200
    response = client.post(path + "/approval-waiting", headers=auth("worker"))
    assert response.status_code == 200, response.text
    assert response.json()["queued"] is not automatic
    assert (app.state.temporal_client.executions[run["workflow_id"]].stage == "published") is automatic


def test_store_outage_fails_closed(settings_gateway, monkeypatch):
    client, app = settings_gateway

    def unavailable(*args):
        raise ConnectionError("offline")

    monkeypatch.setattr(app.state.budget_tracker.client, "get", unavailable)
    assert client.get(PATH, headers=auth("admin")).status_code == 503
    assert chat(client).status_code == 503
    assert app.state.runtime_client.calls == 0


def test_yaml_bootstrap_loads_project_budgets_and_approval_defaults(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("""apiVersion: platform.ai/v1alpha1
kind: SandboxPolicySet
spec:
  policies:
    - sandboxId: team
      projects: [default]
      budgets:
        costLimitUsd: 10
        projectCostLimitsUsd: {default: 2}
      workflows:
        ResearchWorkflow:
          allowedProviders: [openai]
          allowedModels: [primary]
          approvalRequired: true
          approvalThresholdUsd: 0.5
          approverRole: admin
""")
    team = SandboxPolicySet.from_path(path).policies["team"]
    assert team.project_budgets == {"default": 2}
    assert team.workflows["ResearchWorkflow"].approval_threshold_usd == 0.5


def test_removed_route_can_be_inspected_and_reset(settings_gateway):
    client, app = settings_gateway
    assert update(client, {"model_routes.research": "backup"}).status_code == 200
    app.state.model_routing_policy = replace(
        app.state.model_routing_policy, routes=app.state.model_routing_policy.routes[:1],
    )
    assert chat(client).status_code == 400
    assert client.get(PATH, headers=auth("admin")).json()["fields"]["model_routes.research"]["value"] == "backup"
    assert client.delete(PATH + "/model_routes.research", headers={**auth("admin"), "If-Match": "1"}).status_code == 200
    assert chat(client).status_code == 200


def test_effective_layer_does_not_mutate_yaml(settings_gateway):
    _, app = settings_gateway
    team, routing = app.state.sandbox_policy_set.policies["team"], app.state.model_routing_policy
    settings = layer_settings(team, routing, {"overrides": {WORKFLOW + "approval_required": False}})
    assert settings.team.workflows["ResearchWorkflow"].approval_required is False
    assert team.workflows["ResearchWorkflow"].approval_required is True
    assert routing.resolve("research", "").model_id == "primary"
