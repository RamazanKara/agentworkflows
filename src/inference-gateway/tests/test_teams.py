import asyncio
import hashlib
import json
import logging
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.budget import RedisSandboxBudgetTracker
from app.key_records import KeyRecordSet
from app.main import create_app
from app.policy import ModelRoute, ModelRoutingPolicy, SandboxPolicy, SandboxPolicySet, WorkflowPolicy
from app.runtime_client import RuntimeClient
from app.team_budget import RESERVE_COST, SETTLE_COST
from fastapi.testclient import TestClient
from temporalio.exceptions import WorkflowAlreadyStartedError

from tests.gateway_support import _tool_settings
from tests.test_audit_verify import _double_logged_lines, _load_verifier
from tests.test_workflows import RunRedis


class TeamRedis(RunRedis):
    def set(self, key, value):
        self.data[key] = value

    def setnx(self, key, value):
        if key in self.data:
            return 0
        self.set(key, value)
        return 1

    def hset(self, key, field, value):
        self.data.setdefault(key, {})[field] = value

    def zadd(self, key, values):
        self.data.setdefault(key, {}).update(values)

    def zrange(self, key, start, end):
        return sorted(self.data.get(key, {}), key=self.data.get(key, {}).get)[start : end + 1]

    def zrevrange(self, key, start, end):
        return sorted(self.data.get(key, {}), key=self.data.get(key, {}).get, reverse=True)[start : end + 1]

    def rpush(self, key, value):
        self.data.setdefault(key, []).append(value)

    def lrange(self, key, start, end):
        rows = self.data.get(key, [])
        return rows[start:] if end == -1 else rows[start : end + 1]

    def eval(self, script, numkeys, key, *args):
        if script == RESERVE_COST:
            raw = self.data.setdefault(key, {"cost": 0})
            if args[1] and raw["cost"] + args[0] > args[1]:
                return 0
            raw["cost"] += args[0]
            return 1
        if script == SETTLE_COST:
            raw = self.data[key]
            raw["cost"] += args[0]
            for name, value in zip(args[1::2], args[2::2], strict=True):
                raw[name] = raw.get(name, 0) + value
            return 1
        return super().eval(script, numkeys, key, *args)


class Execution:
    def __init__(self):
        self.first_execution_run_id = str(uuid4())
        self.status = "RUNNING"
        self.stage = "awaiting_approval"
        self.reviewer = None
        self.updates = {}

    async def describe(self, **kwargs):
        return SimpleNamespace(status=SimpleNamespace(name=self.status))

    async def query(self, name, **kwargs):
        return {"stage": self.stage, "draft": "Reviewed draft"}

    async def cancel(self, **kwargs):
        self.status = "CANCELED"

    async def result(self, **kwargs):
        return {"status": "published", "publication": "fixture"}

    def get_update_handle(self, id):
        from temporalio.service import RPCError, RPCStatusCode

        async def result(**kwargs):
            if id not in self.updates:
                raise RPCError("unknown update", RPCStatusCode.NOT_FOUND, b"")
            return self.updates[id]

        return SimpleNamespace(result=result)

    async def execute_update(self, name, *, args, id, **kwargs):
        assert name == "review"
        if self.stage != "awaiting_approval":
            return False
        self.reviewer = args[1]
        self.stage = "published" if args[0] else "rejected"
        self.updates[id] = True
        return True


class Temporal:
    def __init__(self):
        self.executions = {}

    async def start_workflow(self, workflow, input, *, id, task_queue, **kwargs):
        assert task_queue == "team-workflows"
        if id in self.executions:
            raise WorkflowAlreadyStartedError(id, workflow, run_id=self.executions[id].first_execution_run_id)
        self.executions[id] = Execution()
        return self.executions[id]

    def get_workflow_handle(self, workflow_id, *, run_id):
        handle = self.executions[workflow_id]
        assert handle.first_execution_run_id == run_id
        return handle


@pytest.fixture
def team_gateway(tmp_path):
    records = []
    for role in ("admin", "builder", "approver", "viewer", "worker", "project", "other"):
        records.append(
            {
                "name": role,
                "sha256": hashlib.sha256(role.encode()).hexdigest(),
                "sandbox": "other" if role == "other" else "team",
                "role": "builder" if role in {"worker", "project"} else "viewer" if role == "other" else role,
                **({"project": "private"} if role == "project" else {}),
                "scopes": ["workflows:execute"] if role == "worker" else [],
            }
        )
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"records": records}))
    settings = _tool_settings(
        api_key_auth_enabled=True,
        api_key_records_path=path,
        sandbox_budget_backend="redis",
        sandbox_budget_enabled=True,
        audit_log_enabled=True,
        allowed_models=("primary", "backup"),
        runtime_max_retries=0,
    )
    app = create_app(settings)
    app.state.budget_tracker = RedisSandboxBudgetTracker(settings, client=TeamRedis())
    policy = WorkflowPolicy.model_validate(
        {
            "allowedProviders": ["openai", "anthropic"],
            "allowedModels": ["primary", "backup"],
            "allowedEgress": ["http://fake"],
        }
    )
    app.state.sandbox_policy_set = SandboxPolicySet(
        {
            "team": SandboxPolicy(
                "team", projects=("default", "private"), cost_limit_usd=1, workflows={"ResearchWorkflow": policy}
            ),
            "other": SandboxPolicy("other", projects=("default",)),
        }
    )
    app.state.model_routing_policy = ModelRoutingPolicy(
        tuple(
            ModelRoute(model, provider, base_url="http://fake", input_usd_per_1k_tokens=1, output_usd_per_1k_tokens=1)
            for model, provider in (("primary", "openai"), ("backup", "anthropic"))
        )
    )
    app.state.temporal_client = Temporal()

    class Runtime:
        calls = 0

        async def chat_completions(self, *args, **kwargs):
            self.calls += 1
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            }

    app.state.runtime_client = Runtime()
    return TestClient(app), app


def auth(role):
    return {"Authorization": f"Bearer {role}"}


def start(client, role="builder", **body):
    return client.post("/v1/workflow-runs", headers=auth(role), json={"input": {"topic": "test"}, **body})


@pytest.mark.parametrize(("role", "code"), [("admin", 201), ("builder", 201), ("approver", 403), ("viewer", 403)])
def test_start_roles_and_verified_team(team_gateway, role, code):
    client, _ = team_gateway
    assert start(client, role).status_code == code
    assert client.post("/v1/workflow-runs", json={"input": {}}).status_code == 401
    assert client.get("/v1/team", headers={**auth(role), "X-Sandbox-ID": "other"}).status_code == 403


@pytest.mark.parametrize(
    "value",
    [
        None,
        "topic",
        {},
        {"topic": ""},
        {"topic": 123},
        {"topic": "ok", "typo": 1},
        {"topic": "ok", "model": None},
        {"topic": "ok", "token_limit": -1},
        {"topic": "ok", "cost_limit_usd": "1"},
        {"topic": "ok", "cost_limit_usd": True},
    ],
)
def test_bad_template_input_does_not_start_a_stuck_execution(team_gateway, value):
    client, app = team_gateway
    response = start(client, input=value)
    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "workflow_input_invalid"
    assert "input.json" in response.json()["detail"]["message"]
    assert not app.state.temporal_client.executions


def test_completed_run_exposes_result_in_detail_not_listing(team_gateway):
    client, app = team_gateway
    run = start(client).json()
    execution = app.state.temporal_client.executions[run["workflow_id"]]
    execution.status = "COMPLETED"
    detail = client.get(f"/v1/workflow-runs/{run['run_id']}", headers=auth("viewer")).json()
    assert detail["result"] == {"status": "published", "publication": "fixture"}
    listing = client.get("/v1/workflow-runs", headers=auth("viewer")).json()
    assert "result" not in listing["runs"][0]


@pytest.mark.parametrize(
    ("workflow", "field"),
    [
        ("WeeklyReportWorkflow", "period"),
        ("IncidentSummaryWorkflow", "incident_id"),
        ("DocumentQAWorkflow", "question"),
    ],
)
def test_gallery_inputs_are_validated_before_temporal(team_gateway, workflow, field):
    client, app = team_gateway
    workflows = app.state.sandbox_policy_set.policies["team"].workflows
    workflows[workflow] = workflows["ResearchWorkflow"]
    for value in ({}, {field: " "}, {field: 12}, {field: "valid", "typo": True}):
        response = start(client, workflow=workflow, input=value)
        assert response.status_code == 422
        assert response.json()["detail"]["reason"] == "workflow_input_invalid"
        assert not app.state.temporal_client.executions
    assert start(client, workflow=workflow, input={field: "valid"}).status_code == 201


def test_start_idempotency_project_and_run_isolation(team_gateway):
    client, app = team_gateway
    request_id = str(uuid4())
    first = start(client, request_id=request_id).json()
    assert start(client, request_id=request_id).json() == first
    assert len(app.state.temporal_client.executions) == 1
    assert start(client, request_id=request_id, input={"topic": "changed"}).status_code == 409
    path = f"/v1/workflow-runs/{first['run_id']}"
    for role in ("other", "project"):
        assert client.get(path, headers=auth(role)).status_code == 404
        assert client.post(path + "/cancel", headers=auth(role)).status_code in {403, 404}
        assert client.get("/v1/workflow-runs", headers=auth(role)).json()["runs"] == []
    assert start(client, "project", project="default").status_code == 404
    assert client.get("/v1/team", headers=auth("project")).json()["projects"] == ["private"]
    assert client.get("/v1/workflow-runs", headers=auth("viewer")).json()["runs"][0]["run_id"] == first["run_id"]


def test_approval_identity_duplicates_and_cancel_retry(team_gateway, caplog):
    caplog.set_level(logging.INFO)
    client, app = team_gateway
    started = start(client).json()
    path = f"/v1/workflow-runs/{started['run_id']}"
    assert client.post(path + "/approve", headers=auth("builder"), json={}).status_code == 403
    assert client.post(path + "/approve", headers=auth("approver"), json={"reviewer": "spoof"}).status_code == 422
    approved = client.post(path + "/approve", headers=auth("approver"), json={}).json()
    assert approved["reviewer"] == "approver"
    assert client.post(path + "/approve", headers=auth("approver"), json={}).status_code == 200
    assert client.post(path + "/approve", headers=auth("approver"), json={"approved": False}).status_code == 409
    assert client.post(path + "/retry", headers=auth("builder")).status_code == 409
    assert client.post(path + "/cancel", headers=auth("viewer")).status_code == 403
    assert client.post(path + "/cancel", headers=auth("builder")).status_code == 200
    retried = client.post(path + "/retry", headers=auth("builder")).json()
    assert retried["run_id"] != started["run_id"]
    assert client.post(path + "/retry", headers=auth("builder")).json() == retried
    timeline = client.get(path, headers=auth("viewer")).json()["timeline"]
    assert timeline[0]["action"] == "approval" and timeline[0]["principal"]["key_id"] == "approver"
    assert len(timeline[0]["receipt_id"]) == 64
    assert timeline[0]["receipt"]["record_hash"] == timeline[0]["receipt_id"]
    assert timeline[0]["receipt"]["approved"] is True
    assert len(app.state.temporal_client.executions) == 2
    verifier = _load_verifier()
    events = verifier.deduplicate(verifier.extract_audit_events(_double_logged_lines(caplog)))
    assert len(events) == len(timeline)
    assert verifier.verify_chain(verifier.group_into_chains(events)[0]).ok


def test_jwt_team_roles_require_a_subject_and_well_formed_claims():
    from app.request_context import _jwt_principal

    assert _jwt_principal({"sub": "alice", "role": "approver"})["role"] == "approver"
    for claims in (
        {"sub": "alice", "role": ["admin"]},
        {"role": "admin"},
        {"sub": "", "role": "admin"},
        {"sub": "alice", "role": "admin", "project": ["private"]},
    ):
        assert "role" not in _jwt_principal(claims)


def test_run_filters_advance_past_empty_pages_and_respect_project(team_gateway):
    client, app = team_gateway
    first = start(client).json()
    second = start(client).json()
    app.state.temporal_client.executions[second["workflow_id"]].stage = "draft"
    page = client.get("/v1/workflow-runs?status=awaiting_approval&limit=1", headers=auth("viewer")).json()
    assert page == {"runs": [], "next_offset": 1}
    page = client.get("/v1/workflow-runs?status=awaiting_approval&limit=1&offset=1", headers=auth("viewer")).json()
    assert page["runs"][0]["run_id"] == first["run_id"]
    assert page["next_offset"] is None
    assert client.get("/v1/workflow-runs?workflow=OtherWorkflow", headers=auth("viewer")).json()["runs"] == []
    assert client.get("/v1/workflow-runs?status=unknown", headers=auth("viewer")).status_code == 422
    assert client.get("/v1/workflow-runs?project=private", headers=auth("viewer")).json()["runs"] == []
    assert client.get("/v1/workflow-runs?project=default", headers=auth("project")).status_code == 404


def test_provider_setup_exposes_references_only_to_admin(team_gateway, monkeypatch):
    client, app = team_gateway
    monkeypatch.setenv("TEAM_TEST_KEY", "fake-secret-never-returned")
    app.state.sandbox_policy_set.policies["team"] = replace(
        app.state.sandbox_policy_set.policies["team"],
        provider_credentials={"openai": "TEAM_TEST_KEY", "anthropic": "TEAM_MISSING_KEY"},
    )
    admin = client.get("/v1/team", headers=auth("admin"))
    assert admin.json()["provider_configuration"] == {
        "openai": {"environment_variable": "TEAM_TEST_KEY", "configured": True},
        "anthropic": {"environment_variable": "TEAM_MISSING_KEY", "configured": False},
    }
    assert "fake-secret-never-returned" not in admin.text
    for role in ("builder", "approver", "viewer", "other"):
        assert "provider_configuration" not in client.get("/v1/team", headers=auth(role)).json()


def test_worker_scope_timeline_and_shared_provider_budget(team_gateway):
    client, app = team_gateway
    started = start(client).json()
    run_id = started["run_id"]
    path = f"/v1/workflow-runs/{run_id}"
    body = {"workflow": "ResearchWorkflow"}
    assert client.put(path, headers=auth("builder"), json=body).status_code == 403
    initialized = client.put(path, headers={**auth("worker"), "X-Workflow-ID": started["workflow_id"]}, json=body)
    assert initialized.status_code == 200, initialized.text
    for model in ("primary", "backup"):
        response = client.post(
            "/v1/chat/completions",
            headers={**auth("worker"), "X-Workflow-Run-ID": run_id, "X-Workflow-Step-ID": model},
            json={"model": model, "max_tokens": 20, "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200, response.text
    report = client.get("/v1/usage", headers=auth("viewer")).json()
    assert report["spend"]["reserved_and_spent_usd"] == 0.01
    assert set(report["providers"]) == {"openai", "anthropic"}
    assert report["spend"]["workflows"]["ResearchWorkflow"] == {"calls": 2, "tokens": 10, "cost_usd": 0.01}
    assert client.get("/v1/usage", headers=auth("project")).json()["providers"] == {}
    assert client.get("/v1/usage", headers=auth("project")).json()["spend"]["workflows"] == {}
    rows = client.get(path, headers=auth("viewer")).json()["timeline"]
    assert len(rows) == 2
    assert {r["provider"] for r in rows} == {"openai", "anthropic"}
    assert all(r["tokens"] == 5 and r["cost_usd"] == 0.005 and r["duration_ms"] >= 0 for r in rows)
    app.state.sandbox_policy_set.policies["team"] = replace(
        app.state.sandbox_policy_set.policies["team"], cost_limit_usd=0.01
    )
    blocked = client.post(
        "/v1/chat/completions",
        headers=auth("builder"),
        json={"model": "backup", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert blocked.status_code == 403 and blocked.json()["detail"]["reason"] == "team_cost_budget_exceeded"
    assert app.state.runtime_client.calls == 2


def test_provider_credentials_are_team_scoped_and_not_forwarded_as_client_auth(monkeypatch):
    monkeypatch.delenv("GLOBAL", raising=False)
    runtime = RuntimeClient(_tool_settings())
    runtime.policy = ModelRoutingPolicy(
        (ModelRoute("primary", "openai", base_url="http://fake", credential_env="GLOBAL", upstream_model="model"),)
    )
    runtime.sandbox_policies = SandboxPolicySet(
        {
            "a": SandboxPolicy("a", projects=("default",), provider_credentials={"openai": "TEAM_A"}),
            "b": SandboxPolicy("b", projects=("default",), provider_credentials={"openai": "TEAM_B"}),
        }
    )
    for team in ("a", "b"):
        monkeypatch.setenv(f"TEAM_{team.upper()}", f"fake-{team}")
        _, _, headers, _ = runtime._request_parts(
            {"model": "primary", "messages": []}, "openai", "chat/completions", {"X-Sandbox-ID": team}
        )
        assert headers["Authorization"] == f"Bearer fake-{team}"
        assert "X-Sandbox-ID" not in headers
    assert asyncio.run(runtime.health("openai"))["status"] == "configured"


def test_fallback_reserves_each_models_estimate_and_retains_failed_cost(team_gateway):
    client, app = team_gateway
    primary, backup = app.state.model_routing_policy.routes
    app.state.model_routing_policy = ModelRoutingPolicy(
        (replace(primary, fallbacks=("backup",)), replace(backup, estimated_chars_per_token=1))
    )
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(team, cost_limit_usd=0.046)
    original = app.state.runtime_client.chat_completions

    async def fail_primary(payload, **kwargs):
        if payload["model"] == "primary":
            response = httpx.Response(503, request=httpx.Request("POST", "http://fake"))
            response.raise_for_status()
        return await original(payload, **kwargs)

    app.state.runtime_client.chat_completions = fail_primary
    body = {"model": "primary", "max_tokens": 20, "messages": [{"role": "user", "content": "hello"}]}
    assert client.post("/v1/chat/completions", headers=auth("builder"), json=body).status_code == 403
    assert app.state.runtime_client.calls == 0
    app.state.sandbox_policy_set.policies["team"] = replace(team, cost_limit_usd=0.1)
    assert client.post("/v1/chat/completions", headers=auth("builder"), json=body).status_code == 200
    spend = client.get("/v1/usage", headers=auth("viewer")).json()["spend"]
    assert spend["reserved_and_spent_usd"] == 0.027
    assert spend["providers"]["openai"]["cost_usd"] == 0.022
    assert spend["providers"]["anthropic"]["cost_usd"] == 0.005


@pytest.mark.parametrize("method", ["chat_completions", "stream_chat_completions", "completions", "embeddings"])
def test_managed_calls_do_not_retry_outside_the_budget_and_receipt(method):
    runtime = RuntimeClient(_tool_settings(runtime_max_retries=2))
    runtime.sandbox_policies = SandboxPolicySet({"team": SandboxPolicy("team", projects=("default",))})
    calls = []

    def unavailable(request):
        calls.append(request)
        return httpx.Response(503)

    async def invoke():
        runtime._client = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
        try:
            result = getattr(runtime, method)({}, headers={"X-Sandbox-ID": "team"})
            if method == "stream_chat_completions":
                async for _ in result:
                    pass
            else:
                await result
        finally:
            await runtime.aclose()

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(invoke())
    assert len(calls) == 1


def test_invalid_roles_and_projects_fail_closed(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"records": [{"sha256": "a" * 64, "sandbox": "team", "role": "owner"}]}))
    with pytest.raises(ValueError, match="role"):
        KeyRecordSet.from_path(path)


def test_temporal_outage_is_actionable(team_gateway, monkeypatch):
    from temporalio.service import RPCError, RPCStatusCode

    client, app = team_gateway

    async def unavailable(*args, **kwargs):
        raise RPCError("offline", RPCStatusCode.UNAVAILABLE, b"")

    monkeypatch.setattr(app.state.temporal_client, "start_workflow", unavailable)
    response = start(client)
    assert response.status_code == 503
    assert "same request_id" in response.json()["error"]["message"]


def test_approval_recovers_accepted_update_after_response_timeout(team_gateway, monkeypatch):
    from temporalio.client import WorkflowUpdateRPCTimeoutOrCancelledError

    client, app = team_gateway
    started = start(client).json()
    handle = app.state.temporal_client.executions[started["workflow_id"]]
    original = handle.execute_update

    async def lost_response(*args, **kwargs):
        await original(*args, **kwargs)
        raise WorkflowUpdateRPCTimeoutOrCancelledError()

    monkeypatch.setattr(handle, "execute_update", lost_response)
    path = f"/v1/workflow-runs/{started['run_id']}/approve"
    response = client.post(path, headers=auth("approver"), json={})
    assert response.status_code == 503 and response.json()["error"]["code"] == "approval_pending"
    response = client.post(path, headers=auth("approver"), json={})
    assert response.status_code == 200 and response.json()["reviewer"] == "approver"
    assert len(handle.updates) == 1


def test_early_approval_can_be_retried_when_draft_is_ready(team_gateway):
    client, app = team_gateway
    started = start(client).json()
    handle = app.state.temporal_client.executions[started["workflow_id"]]
    handle.stage = "research"
    path = f"/v1/workflow-runs/{started['run_id']}/approve"
    assert client.post(path, headers=auth("approver"), json={}).status_code == 409
    handle.stage = "awaiting_approval"
    assert client.post(path, headers=auth("approver"), json={}).status_code == 200


def test_key_override_cannot_raise_team_token_budget_or_charge_unsent_calls(team_gateway):
    client, app = team_gateway
    app.state.sandbox_policy_set.policies["team"] = replace(
        app.state.sandbox_policy_set.policies["team"], estimated_token_budget=10
    )
    app.state.key_record_set = KeyRecordSet(
        tuple(
            replace(record, estimated_token_budget=100000) if record.key_id == "builder" else record
            for record in app.state.key_record_set.records
        )
    )
    response = client.post(
        "/v1/chat/completions",
        headers=auth("builder"),
        json={"model": "primary", "max_tokens": 20, "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 429
    assert app.state.runtime_client.calls == 0
    assert client.get("/v1/usage", headers=auth("viewer")).json()["spend"]["reserved_and_spent_usd"] == 0


def test_managed_teams_require_durable_budgets_and_receipts(tmp_path):
    path = tmp_path / "teams.yaml"
    path.write_text(
        "apiVersion: platform.ai/v1alpha1\nkind: SandboxPolicySet\n"
        "spec:\n  policies:\n    - sandboxId: team\n      projects: [default]\n"
    )
    with pytest.raises(ValueError, match="Team projects require"):
        create_app(_tool_settings(sandbox_policy_path=path))
