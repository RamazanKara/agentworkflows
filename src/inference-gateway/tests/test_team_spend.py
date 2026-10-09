# ruff: noqa: F811
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from app.policy import TeamNotifications
from app.team_budget import cost_key, month_window
from app.team_spend import notify_spend
from fastapi import Request
from redis.exceptions import ConnectionError

from tests.test_team_settings import chat, settings_gateway, update  # noqa: F401
from tests.test_teams import auth, team_gateway  # noqa: F401


def request_for(app):
    return Request(
        {
            "type": "http",
            "app": app,
            "state": {
                "sandbox_id": "team",
                "sandbox_bound": True,
                "principal": {"role": "admin"},
            },
        }
    )


def test_limits_soft_alert_and_hard_rejection(settings_gateway):
    client, app = settings_gateway
    assert update(client, {"soft_cost_limit_usd": 0.01, "cost_limit_usd": 0.03}).status_code == 200
    assert chat(client).status_code == 200
    status = client.get("/v1/team/spend", headers=auth("viewer"))
    assert status.headers["cache-control"] == "no-store"
    body = status.json()
    assert body["reserved_and_spent_usd"] == 0.005
    assert body["status"] == "ok"
    assert len(body["alerts"]) == 1
    assert body["alerts"][0]["level"] == "soft"
    assert body["alerts"][0]["webhook_status"] == "disabled"
    assert chat(client).status_code == 200
    denied = chat(client)
    assert denied.status_code == 429
    assert denied.json()["detail"]["reason"] == "team_cost_budget_exceeded"
    assert 1 <= int(denied.headers["retry-after"]) <= 31 * 86400
    assert app.state.runtime_client.calls == 2
    alerts = client.get("/v1/team/spend", headers=auth("viewer")).json()["alerts"]
    assert [row["level"] for row in alerts] == ["soft", "hard"]
    assert alerts[0]["id"] == body["alerts"][0]["id"]
    assert client.get("/v1/team/spend", headers=auth("project")).status_code == 403
    assert client.get("/v1/team/spend", headers=auth("other")).json()["alerts"] == []
    assert update(client, {"cost_limit_usd": 0.1}, revision=1).status_code == 200
    assert chat(client).status_code == 200


@pytest.mark.parametrize(
    "fields",
    [
        {"soft_cost_limit_usd": -1},
        {"soft_cost_limit_usd": True},
        {"soft_cost_limit_usd": "1"},
        {"soft_cost_limit_usd": 2},
        {"soft_cost_limit_usd": 0.5, "cost_limit_usd": 0.4},
        {"capture_content": "unexpected"},
        {"workflows.ResearchWorkflow.capture_content": None},
    ],
)
def test_invalid_limits_and_capture_are_atomic(settings_gateway, fields):
    client, _ = settings_gateway
    response = update(client, fields)
    assert response.status_code == 422
    assert response.json()["detail"]["fields"]
    assert client.get("/v1/team/settings", headers=auth("admin")).json()["revision"] == 0


def test_zero_disabled_and_settings_revision(settings_gateway):
    client, _ = settings_gateway
    assert update(client, {"soft_cost_limit_usd": 0, "cost_limit_usd": 0}).status_code == 200
    assert chat(client).status_code == 429
    assert update(client, {"soft_cost_limit_usd": None, "cost_limit_usd": None}, revision=1).status_code == 200
    assert chat(client).status_code == 200
    assert update(client, {"cost_limit_usd": 0}, revision=1).status_code == 409
    assert (
        client.patch(
            "/v1/team/settings", headers={**auth("builder"), "If-Match": "2"}, json={"fields": {"cost_limit_usd": 0}}
        ).status_code
        == 403
    )


def test_utc_month_reset_clears_alerts_and_spend(settings_gateway, monkeypatch):
    client, _ = settings_gateway
    assert update(client, {"cost_limit_usd": 0}).status_code == 200
    assert chat(client).status_code == 429

    class February(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2028, 2, 1, tzinfo=UTC)

    monkeypatch.setattr("app.team_budget.datetime", February)
    start, seconds, retry = month_window()
    assert seconds == retry == 29 * 86400
    status = client.get("/v1/team/spend", headers=auth("admin")).json()
    assert status["window_start"] == start
    assert status["alerts"] == [] and status["reserved_and_spent_usd"] == 0


def test_concurrent_hard_rejections_never_call_provider_or_duplicate_alert(settings_gateway):
    client, app = settings_gateway
    assert update(client, {"cost_limit_usd": 0.001}).status_code == 200
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert set(pool.map(lambda _: chat(client).status_code, range(12))) == {429}
    assert app.state.runtime_client.calls == 0
    assert len(client.get("/v1/team/spend", headers=auth("admin")).json()["alerts"]) == 1


def test_webhook_retry_and_idempotency_without_email(settings_gateway, monkeypatch):
    client, app = settings_gateway
    request = request_for(app)
    team = app.state.sandbox_policy_set.policies["team"]
    app.state.sandbox_policy_set.policies["team"] = replace(
        team,
        notifications=TeamNotifications.model_validate(
            {
                "webhookEnv": "TEAM_WEBHOOK",
                "consoleUrl": "https://example.test/console/",
            }
        ),
    )
    assert update(client, {"cost_limit_usd": 0}).status_code == 200
    assert chat(client).status_code == 429
    send = AsyncMock(side_effect=[httpx.ConnectError("destination secret"), None])
    monkeypatch.setattr("app.team_spend.send_notification", send)
    asyncio.run(notify_spend(request))
    asyncio.run(notify_spend(request))
    assert send.await_count == 1
    key, _ = cost_key(request)
    raw = app.state.budget_tracker.client.data[key]
    state = json.loads(raw["delivery.hard"])
    state["next_at"] = 0
    raw["delivery.hard"] = json.dumps(state)
    asyncio.run(notify_spend(request))
    asyncio.run(notify_spend(request))
    assert send.await_count == 2
    first, second = send.await_args_list
    assert first.args[1] == second.args[1] == "webhook"
    assert first.args[2]["id"] == second.args[2]["id"]
    assert first.args[2]["console_url"] == "https://example.test/console#costs"
    status = client.get("/v1/team/spend", headers=auth("admin")).json()
    assert status["alerts"][0]["webhook_status"] == "delivered"
    assert status["alerts"][0]["attempts"] == 2


def test_store_outage_fails_closed(settings_gateway, monkeypatch):
    client, app = settings_gateway
    monkeypatch.setattr(app.state.budget_tracker.client, "hgetall", lambda _: (_ for _ in ()).throw(ConnectionError()))
    assert client.get("/v1/team/spend", headers=auth("admin")).status_code == 503
