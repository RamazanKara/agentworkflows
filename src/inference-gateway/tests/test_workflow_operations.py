# ruff: noqa: F811  (shared gateway fixtures are imported, then requested by name)
import pytest
from temporalio.service import RPCError, RPCStatusCode

from tests.test_teams import auth, start, team_gateway  # noqa: F401


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "CANCELED"])
def test_cancelling_a_finished_run_is_a_conflict_not_a_missing_run(team_gateway, monkeypatch, status):
    client, app = team_gateway
    run = start(client).json()
    execution = app.state.temporal_client.executions[run["workflow_id"]]
    execution.status = status

    async def closed(**kwargs):
        raise RPCError("workflow execution already completed", RPCStatusCode.NOT_FOUND, b"")

    monkeypatch.setattr(execution, "cancel", closed)
    response = client.post(f"/v1/workflow-runs/{run['run_id']}/cancel", headers=auth("builder"))
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "workflow_not_cancellable"
    assert status.lower() in response.json()["detail"]["message"]


def test_cancelling_a_running_run_records_one_receipt(team_gateway):
    client, _ = team_gateway
    run = start(client).json()
    path = f"/v1/workflow-runs/{run['run_id']}"
    assert client.post(path + "/cancel", headers=auth("viewer")).status_code == 403
    response = client.post(path + "/cancel", headers=auth("builder"))
    assert response.json() == {"run_id": run["run_id"], "status": "cancellation_requested"}
    timeline = client.get(path, headers=auth("viewer")).json()["timeline"]
    assert [step["action"] for step in timeline] == ["cancel"]


def test_vanished_execution_is_still_reported_missing(team_gateway, monkeypatch):
    client, app = team_gateway
    run = start(client).json()
    execution = app.state.temporal_client.executions[run["workflow_id"]]

    async def missing(**kwargs):
        raise RPCError("not found", RPCStatusCode.NOT_FOUND, b"")

    monkeypatch.setattr(execution, "cancel", missing)
    monkeypatch.setattr(execution, "describe", missing)
    response = client.post(f"/v1/workflow-runs/{run['run_id']}/cancel", headers=auth("builder"))
    assert response.status_code == 404 and response.json()["detail"]["reason"] == "workflow_run_missing"
