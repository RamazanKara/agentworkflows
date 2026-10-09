# ruff: noqa: F811
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.metrics import TEAM_SPEND
from cryptography.fernet import Fernet
from fastapi.responses import StreamingResponse
from temporalio.service import RPCError, RPCStatusCode

from tests.test_managed_keys import AuthRedis
from tests.test_teams import auth, start, team_gateway  # noqa: F401


def test_template_versions_install_idempotently_and_stamp_runs(team_gateway):
    client, app = team_gateway
    path = "/v1/workflow-templates/research/install"
    assert client.get("/v1/workflow-templates", headers=auth("viewer")).json()[0]["installed_version"] is None
    assert client.post(path, headers=auth("builder"), json={"version": "0.9.0"}).status_code == 403
    assert client.post(path, headers=auth("admin"), json={"version": "latest"}).status_code == 422
    installed = client.post(path, headers=auth("admin"), json={"version": "0.9.0"})
    assert installed.status_code == 200
    assert client.post(path, headers=auth("admin"), json={"version": "0.9.0"}).json() == installed.json()
    assert app.state.storage.get_settings("team")["revision"] == 1
    assert (
        client.post(
            "/v1/workflow-templates/code-review/install", headers=auth("admin"), json={"version": "0.9.0"}
        ).status_code
        == 409
    )
    run = start(client).json()
    assert app.state.storage.get_run("team", run["run_id"])["template"] == installed.json()
    assert app.state.storage.get_settings("other") is None
    client.patch(
        "/v1/team/settings", headers={**auth("admin"), "If-Match": "1"}, json={"fields": {"cost_limit_usd": 2}}
    )
    assert app.state.storage.get_settings("team")["templates"]["research"] == installed.json()


def test_secret_rotation_encryption_scope_and_receipts(team_gateway, caplog):
    client, app = team_gateway
    app.state.settings = replace(app.state.settings, workflow_secrets_key=Fernet.generate_key().decode())
    path = "/v1/workflows/ResearchWorkflow/secrets/publishing-token"
    secret = "private-provider-credential"
    caplog.set_level("INFO")
    assert client.put(path, headers=auth("builder"), json={"value": secret, "expected_version": 0}).status_code == 403
    first = client.put(path, headers=auth("admin"), json={"value": secret, "expected_version": 0})
    assert first.status_code == 200, first.text
    assert secret not in str(app.state.budget_tracker.client.data)
    assert secret not in first.text
    assert client.put(path, headers=auth("admin"), json={"value": "new", "expected_version": 0}).status_code == 409
    assert (
        client.put(path, headers=auth("admin"), json={"value": "rotated-value", "expected_version": 1}).json()[
            "version"
        ]
        == 2
    )
    run = start(client).json()
    resolve = f"/v1/workflow-runs/{run['run_id']}/secrets/publishing-token/resolve"
    headers = {**auth("worker"), "X-Workflow-Run-ID": run["run_id"], "X-Workflow-Step-ID": "publish"}
    assert client.post(resolve, headers=auth("admin"), json={}).status_code == 403
    assert client.post(resolve, headers=headers, json={}).json()["value"] == "rotated-value"
    assert client.post(resolve, headers={**headers, "X-Sandbox-ID": "other"}, json={}).status_code == 403
    app.state.temporal_client.executions[run["workflow_id"]].status = "COMPLETED"
    assert client.post(resolve, headers=headers, json={}).status_code == 409
    events = [json.loads(row.message) for row in caplog.records if row.name == "agentworkflows.audit"]
    assert {"workflow_secret_created", "workflow_secret_rotated", "workflow_secret_accessed"} <= {
        row["action_type"] for row in events
    }
    assert secret not in caplog.text and "rotated-value" not in caplog.text
    assert "rotated-value" not in client.get(path.rsplit("/", 1)[0], headers=auth("admin")).text


def test_secret_fail_closed_without_key_and_for_revoked_workflow(team_gateway):
    client, app = team_gateway
    path = "/v1/workflows/ResearchWorkflow/secrets/token"
    assert client.put(path, headers=auth("admin"), json={"value": "test", "expected_version": 0}).status_code == 503
    assert app.state.storage.get_settings("team") is None
    assert client.get("/v1/workflows/Unknown/secrets", headers=auth("admin")).status_code == 404


def test_retention_is_revision_checked_team_scoped_and_preserved(team_gateway):
    client, app = team_gateway
    path = "/v1/team/retention"
    policy = {"run_seconds": 120, "content_seconds": 60, "audit_seconds": 180}
    assert client.get(path, headers=auth("viewer")).status_code == 403
    assert client.put(path, headers={**auth("admin"), "If-Match": "0"}, json=policy).json() == {**policy, "revision": 1}
    assert client.put(path, headers={**auth("admin"), "If-Match": "0"}, json=policy).status_code == 409
    assert (
        client.put(path, headers={**auth("admin"), "If-Match": "1"}, json={**policy, "run_seconds": 0}).status_code
        == 422
    )
    client.patch(
        "/v1/team/settings", headers={**auth("admin"), "If-Match": "1"}, json={"fields": {"cost_limit_usd": 2}}
    )
    assert client.get(path, headers=auth("admin")).json() == {**policy, "revision": 2}
    started = start(client).json()
    app.state.temporal_client.executions[started["workflow_id"]].status = "COMPLETED"
    client.get(f"/v1/workflow-runs/{started['run_id']}", headers=auth("viewer"))
    prefix = app.state.settings.sandbox_budget_key_prefix
    ttl = app.state.budget_tracker.client.ttl(f"{prefix}:workflow:team:{started['run_id']}:metadata")
    assert 115 <= ttl <= 121
    assert app.state.storage.get_settings("other") is None


def data_fixture(app):
    app.state.budget_tracker.client = AuthRedis()
    temporal = app.state.temporal_client
    temporal.namespace = "default"
    temporal.workflow_service = SimpleNamespace(delete_workflow_execution=AsyncMock())

    async def listing(**kwargs):
        assert kwargs["query"] == 'WorkflowId STARTS_WITH "team/"'
        for workflow_id, execution in temporal.executions.items():
            execution.fetch_history = AsyncMock(return_value=SimpleNamespace(to_json=lambda: '{"events": []}'))
            yield SimpleNamespace(
                id=workflow_id, run_id=execution.first_execution_run_id, status=SimpleNamespace(name=execution.status)
            )

    temporal.list_workflows = listing


def test_export_and_erasure_leave_other_team_data_and_block_new_work(team_gateway):
    client, app = team_gateway
    data_fixture(app)
    TEAM_SPEND.labels("team").set(12)
    prefix = app.state.settings.sandbox_budget_key_prefix
    store = app.state.budget_tracker.client
    store.set(f"{prefix}:workflow:other:keep:metadata", '{"input":"other data"}')
    app.state.settings = replace(app.state.settings, workflow_secrets_key=Fernet.generate_key().decode())
    client.put(
        "/v1/workflows/ResearchWorkflow/secrets/token",
        headers=auth("admin"),
        json={"value": "private-value", "expected_version": 0},
    )
    run = start(client).json()
    exported = client.get("/v1/team/data/export", headers=auth("admin"))
    assert exported.status_code == 200, exported.text
    assert exported.json()["runs"][0]["metadata"]["input"] == {"topic": "test"}
    assert exported.json()["temporal_histories"][0]["run_id"] == run["run_id"]
    assert (
        "private-value" not in exported.text and "ciphertext" not in exported.text and "other data" not in exported.text
    )
    path = "/v1/team/data"
    assert client.request("DELETE", path, headers=auth("admin"), json={"confirm_team": "other"}).status_code == 422
    assert client.request("DELETE", path, headers=auth("admin"), json={"confirm_team": "team"}).status_code == 409
    assert client.get("/v1/team", headers=auth("admin")).status_code == 200
    app.state.temporal_client.executions[run["workflow_id"]].status = "CANCELED"
    erased = client.request("DELETE", path, headers=auth("admin"), json={"confirm_team": "team"})
    assert erased.status_code == 200, erased.text
    assert erased.json()["status"] == "erased"
    assert not any(sample.labels.get("team") == "team" for metric in TEAM_SPEND.collect() for sample in metric.samples)
    assert app.state.storage.get_run("team", run["run_id"]) is None
    assert app.state.storage.get_settings("team") is None
    assert store.get(f"{prefix}:workflow:other:keep:metadata") == '{"input":"other data"}'
    assert start(client).status_code == 409
    assert client.get("/v1/team/data/export", headers=auth("admin")).status_code == 409
    assert client.get(path, headers=auth("admin")).json()["status"] == "erased"
    app.state.temporal_client.workflow_service.delete_workflow_execution.assert_awaited_once()


def test_erasure_does_not_race_requests_and_failure_remains_resumable(team_gateway):
    client, app = team_gateway
    data_fixture(app)
    prefix = app.state.settings.sandbox_budget_key_prefix
    active = f"{prefix}:team-data:team:active"
    app.state.budget_tracker.client.set(active, 1)
    assert (
        client.request("DELETE", "/v1/team/data", headers=auth("admin"), json={"confirm_team": "team"}).status_code
        == 409
    )
    app.state.budget_tracker.client.delete(active)
    run = start(client).json()
    app.state.temporal_client.executions[run["workflow_id"]].status = "COMPLETED"
    service = app.state.temporal_client.workflow_service
    service.delete_workflow_execution.side_effect = RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
    assert (
        client.request("DELETE", "/v1/team/data", headers=auth("admin"), json={"confirm_team": "team"}).status_code
        == 503
    )
    assert start(client).status_code == 409
    assert app.state.storage.get_run("team", run["run_id"])
    app.state.temporal_client.executions[run["workflow_id"]].status = "RUNNING"
    assert (
        client.request("DELETE", "/v1/team/data", headers=auth("admin"), json={"confirm_team": "team"}).status_code
        == 409
    )
    assert client.get("/v1/team/data", headers=auth("admin")).json()["status"] == "erasing"
    app.state.temporal_client.executions[run["workflow_id"]].status = "COMPLETED"
    service.delete_workflow_execution.side_effect = None
    assert (
        client.request("DELETE", "/v1/team/data", headers=auth("admin"), json={"confirm_team": "team"}).json()["status"]
        == "erased"
    )


def test_erasure_checks_start_intents_even_before_temporal_visibility(team_gateway):
    client, app = team_gateway
    data_fixture(app)
    run = start(client).json()

    async def not_indexed(**kwargs):
        for row in []:
            yield row

    app.state.temporal_client.list_workflows = not_indexed
    prefix = app.state.settings.sandbox_budget_key_prefix
    app.state.budget_tracker.client.delete(f"{prefix}:workflow:team:{run['run_id']}:metadata")
    response = client.request("DELETE", "/v1/team/data", headers=auth("admin"), json={"confirm_team": "team"})
    assert response.status_code == 409
    assert "Cancel running" in response.text
    assert client.get("/v1/team", headers=auth("admin")).status_code == 200


def test_erasure_waits_for_stream_body_completion(team_gateway):
    client, app = team_gateway
    data_fixture(app)
    started, release = Event(), Event()

    @app.get("/v1/test-stream")
    async def stream():
        async def body():
            yield b"first"
            started.set()
            await asyncio.to_thread(release.wait, 10)
            yield b"last"

        return StreamingResponse(body())

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.get, "/v1/test-stream", headers=auth("admin"))
        try:
            assert started.wait(5)
            response = client.request("DELETE", "/v1/team/data", headers=auth("admin"), json={"confirm_team": "team"})
            assert response.status_code == 409
            assert "requests are still active" in response.text
        finally:
            release.set()
        assert pending.result(timeout=5).content == b"firstlast"
    response = client.request("DELETE", "/v1/team/data", headers=auth("admin"), json={"confirm_team": "team"})
    assert response.json()["status"] == "erased"


def test_concurrent_erasure_requests_are_serialized(team_gateway):
    client, app = team_gateway
    data_fixture(app)
    run = start(client).json()
    app.state.temporal_client.executions[run["workflow_id"]].status = "COMPLETED"
    started, release = Event(), Event()

    async def delete_execution(*args, **kwargs):
        started.set()
        await asyncio.to_thread(release.wait, 10)

    service = app.state.temporal_client.workflow_service
    service.delete_workflow_execution.side_effect = delete_execution
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            client.request, "DELETE", "/v1/team/data", headers=auth("admin"), json={"confirm_team": "team"}
        )
        try:
            assert started.wait(5)
            response = client.request("DELETE", "/v1/team/data", headers=auth("admin"), json={"confirm_team": "team"})
            assert response.status_code == 409
            assert "already running" in response.text
        finally:
            release.set()
        assert pending.result(timeout=5).json()["status"] == "erased"
    service.delete_workflow_execution.assert_awaited_once()


@pytest.mark.parametrize("path", ["/v1/team/data", "/v1/team/data/export", "/v1/team/retention", "/v1/team/telemetry"])
def test_team_administration_denies_unprivileged_callers(team_gateway, path):
    client, _ = team_gateway
    for role in ("viewer", "builder", "approver", "worker", "project", "other"):
        assert client.get(path, headers=auth(role)).status_code == 403


@pytest.mark.parametrize(
    "template_id, workflow",
    [
        ("release-notes", "ReleaseNotesWorkflow"),
        ("meeting-actions", "MeetingActionsWorkflow"),
        ("security-questionnaire", "SecurityQuestionnaireWorkflow"),
    ],
)
def test_new_gallery_versions_keep_operator_policy_authoritative(team_gateway, template_id, workflow):
    client, app = team_gateway
    catalog = client.get("/v1/workflow-templates", headers=auth("viewer")).json()
    assert len(catalog) == 9
    template = next(item for item in catalog if item["id"] == template_id)
    assert template["version"] == "1.0.0-rc.4" and not template["installable"]
    path = f"/v1/workflow-templates/{template_id}/install"
    assert client.post(path, headers=auth("admin"), json={"version": "0.9.0"}).status_code == 422
    assert client.post(path, headers=auth("admin"), json={"version": template["version"]}).status_code == 409
    team = app.state.sandbox_policy_set.policies["team"]
    team.workflows[workflow] = team.workflows["ResearchWorkflow"]
    installed = client.post(path, headers=auth("admin"), json={"version": template["version"]})
    assert installed.status_code == 200
    assert client.post(path, headers=auth("admin"), json={"version": template["version"]}).json() == installed.json()
    assert app.state.storage.get_settings("team")["revision"] == 1
