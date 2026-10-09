# ruff: noqa: F811
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.policy import WorkflowPolicy
from pydantic import ValidationError

from tests.test_team_settings import WORKFLOW, initialized_run, update
from tests.test_teams import auth, start, team_gateway  # noqa: F401


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("required_approvals", 0),
        ("required_approvals", 11),
        ("required_approvals", True),
        ("required_approvals", 1.5),
        ("approval_timeout_seconds", 59),
        ("approval_timeout_seconds", 604801),
        ("approval_timeout_seconds", "60"),
    ],
)
def test_approval_policy_validation_in_settings_and_yaml(team_gateway, field, value):
    client, _ = team_gateway
    assert update(client, {WORKFLOW + field: value}).status_code == 422
    alias = "requiredApprovals" if field == "required_approvals" else "approvalTimeoutSeconds"
    with pytest.raises(ValidationError):
        WorkflowPolicy.model_validate({"allowedProviders": [], "allowedModels": [], alias: value})


def test_approval_policy_snapshot_survives_settings_changes_and_start_retry(team_gateway):
    client, _ = team_gateway
    fields = {"required_approvals": 2, "approval_timeout_seconds": 60, "approver_role": "admin"}
    assert update(client, {WORKFLOW + key: value for key, value in fields.items()}).status_code == 200
    request_id = str(uuid4())
    run = start(client, request_id=request_id).json()
    path = f"/v1/workflow-runs/{run['run_id']}"
    assert (
        update(client, {WORKFLOW + "required_approvals": 1, WORKFLOW + "approver_role": "approver"}, 1).status_code
        == 200
    )
    assert start(client, request_id=request_id).json()["run_id"] == run["run_id"]
    gate = client.post(path + "/approval-waiting", headers=auth("worker"), json={"policy_version": 1})
    assert gate.status_code == 200, gate.text
    assert gate.json() == {"queued": True, "approval_required": True, "policy_version": 1, **fields}
    assert client.post(path + "/approval-waiting", headers=auth("worker")).status_code == 409
    assert client.post(path + "/approve", headers=auth("approver"), json={}).status_code == 403
    assert client.post(path + "/approve", headers=auth("admin"), json={}).status_code == 409


def test_approval_gate_requires_worker_and_same_team_project(team_gateway):
    client, _ = team_gateway
    _, path, _ = initialized_run(client)
    for role in ("admin", "builder", "approver", "viewer", "project", "other"):
        assert (
            client.post(path + "/approval-waiting", headers=auth(role), json={"policy_version": 1}).status_code == 403
        )
    assert (
        client.post(path + "/approval-waiting", headers=auth("worker"), json={"policy_version": 2}).status_code == 422
    )
    assert client.post(path + "/approve", headers=auth("project"), json={}).status_code == 403


def test_quorum_votes_use_verified_identity_and_idempotent_temporal_updates(team_gateway, monkeypatch):
    pytest.importorskip('agentworkflows.workflows', reason='SDK package is installed only in the SDK/worker environment')
    from agentworkflows.workflows import ApprovalWorkflow

    client, app = team_gateway
    assert update(client, {WORKFLOW + "required_approvals": 2}).status_code == 200
    run, path, _ = initialized_run(client)
    handle = app.state.temporal_client.executions[run["workflow_id"]]
    gate = ApprovalWorkflow()
    gate.stage, gate.required_approvals = "awaiting_approval", 2
    monkeypatch.setattr(
        handle, "query", AsyncMock(return_value={"stage": "awaiting_approval", "approval_policy_version": 1})
    )

    async def review(name, *, args, id, **kwargs):
        accepted = gate.review(*args)
        handle.updates[id] = accepted
        return accepted

    monkeypatch.setattr(handle, "execute_update", review)
    first = client.post(path + "/approve", headers=auth("approver"), json={"approved": True})
    assert first.status_code == 200 and first.json()["reviewer"] == "approver"
    assert gate.decision is None
    assert client.post(path + "/approve", headers=auth("approver"), json={}).status_code == 200
    assert client.post(path + "/approve", headers=auth("approver"), json={"approved": False}).status_code == 409
    assert gate.approved_by == ["approver"] and gate.decision is None
    assert client.post(path + "/approve", headers=auth("admin"), json={"reviewer": "forged"}).status_code == 422
    assert client.post(path + "/approve", headers=auth("admin"), json={}).status_code == 200
    assert gate.decision is True and gate.approved_by == ["approver", "admin"]


def test_versioned_automatic_approval_does_not_wait_for_an_update(team_gateway, monkeypatch):
    client, app = team_gateway
    assert (
        update(client, {WORKFLOW + "approval_required": False, WORKFLOW + "required_approvals": 2}).status_code == 200
    )
    run, path, _ = initialized_run(client)
    handle = app.state.temporal_client.executions[run["workflow_id"]]
    execute = AsyncMock(side_effect=AssertionError("Worker applies automatic approval from activity result"))
    monkeypatch.setattr(handle, "execute_update", execute)
    response = client.post(path + "/approval-waiting", headers=auth("worker"), json={"policy_version": 1})
    assert response.status_code == 200
    assert response.json()["approval_required"] is False and response.json()["queued"] is False
    execute.assert_not_awaited()
