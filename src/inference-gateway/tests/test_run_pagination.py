# ruff: noqa: F811  (shared gateway fixtures are imported, then requested by name)
import base64
import json

import pytest

from tests.test_teams import auth, start, team_gateway  # noqa: F401


def test_cursor_survives_new_runs_and_expired_anchor(team_gateway):
    client, app = team_gateway
    runs = [start(client).json() for _ in range(4)]
    page = client.get("/v1/workflow-runs?limit=2", headers=auth("viewer")).json()
    assert [row["run_id"] for row in page["runs"]] == [run["run_id"] for run in reversed(runs[2:])]
    assert page["next_offset"] == 2
    start(client)
    store = app.state.budget_tracker.client
    prefix = app.state.settings.sandbox_budget_key_prefix
    for run in runs[1:]:
        store.delete(f"{prefix}:workflow:team:{run['run_id']}:metadata")
    # Another reader prunes the expired cursor and the run immediately after it.
    client.get("/v1/workflow-runs", headers=auth("viewer"))
    page = client.get("/v1/workflow-runs", params={"limit": 2, "cursor": page["next_cursor"]},
                      headers=auth("viewer")).json()
    assert [row["run_id"] for row in page["runs"]] == [runs[0]["run_id"]]
    assert page["next_cursor"] is None and page["next_offset"] is None


def test_pre_070_cursor_still_pages_unfiltered_history(team_gateway):
    client, _ = team_gateway
    first = start(client).json()
    start(client)
    page = client.get("/v1/workflow-runs?limit=1", headers=auth("viewer")).json()
    cursor = json.loads(base64.urlsafe_b64decode(page["next_cursor"]))
    del cursor["trigger"]
    response = client.get("/v1/workflow-runs", headers=auth("viewer"), params={
        "cursor": base64.urlsafe_b64encode(json.dumps(cursor).encode()).decode(),
    })
    assert response.status_code == 200
    assert [row["run_id"] for row in response.json()["runs"]] == [first["run_id"]]


def test_cursor_orders_tied_timestamps_and_advances_through_empty_pages(team_gateway):
    client, app = team_gateway
    runs = [start(client).json() for _ in range(4)]
    store = app.state.budget_tracker.client
    key = f"{app.state.settings.sandbox_budget_key_prefix}:runs:team:default"
    store.zadd(key, {run["run_id"]: 100.25 for run in runs})
    oldest = min(runs, key=lambda run: run["run_id"])
    app.state.temporal_client.executions[oldest["workflow_id"]].status = "COMPLETED"
    params = {"limit": 1, "status": "completed"}
    cursors = []
    for index in range(4):
        response = client.get("/v1/workflow-runs", params=params, headers=auth("viewer"))
        assert response.status_code == 200
        page = response.json()
        if index < 3:
            assert page["runs"] == []
            assert page["next_cursor"] and page["next_cursor"] not in cursors
            cursors.append(page["next_cursor"])
            params["cursor"] = page["next_cursor"]
        else:
            assert [row["run_id"] for row in page["runs"]] == [oldest["run_id"]]
            assert page["next_cursor"] is None


@pytest.mark.parametrize("changes,identity,expected", [
    ({"project": "private"}, "viewer", 422),
    ({"workflow": "Other"}, "viewer", 422),
    ({"status": "failed"}, "viewer", 422),
    ({"offset": 1}, "viewer", 422),
    ({}, "other", 422),
    ({"project": "default"}, "project", 404),
])
def test_cursor_cannot_change_team_project_filters_or_offset(team_gateway, changes, identity, expected):
    client, _ = team_gateway
    start(client)
    start(client)
    page = client.get("/v1/workflow-runs?limit=1", headers=auth("viewer")).json()
    response = client.get("/v1/workflow-runs", params={"cursor": page["next_cursor"], **changes},
                          headers=auth(identity))
    assert response.status_code == expected
    if expected == 422:
        assert response.json()["detail"]["reason"] == "run_cursor_invalid"


@pytest.mark.parametrize("cursor", ["!", "null", "", "A" * 2049, base64.urlsafe_b64encode(b"{}").decode()])
def test_malformed_cursor_is_a_validation_error(team_gateway, cursor):
    client, _ = team_gateway
    assert client.get("/v1/workflow-runs", params={"cursor": cursor}, headers=auth("viewer")).status_code == 422


@pytest.mark.parametrize("changes", [{"created_at": float("inf")}, {"created_at": -1}, {"version": 2},
                                     {"run_id": "not-a-uuid"}])
def test_cursor_validates_position_and_version(team_gateway, changes):
    client, _ = team_gateway
    start(client)
    start(client)
    page = client.get("/v1/workflow-runs?limit=1", headers=auth("viewer")).json()
    cursor = json.loads(base64.urlsafe_b64decode(page["next_cursor"]))
    cursor.update(changes)
    value = base64.urlsafe_b64encode(json.dumps(cursor).encode()).decode()
    response = client.get("/v1/workflow-runs", params={"cursor": value}, headers=auth("viewer"))
    assert response.status_code == 422
