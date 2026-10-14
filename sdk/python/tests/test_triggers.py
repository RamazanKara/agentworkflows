import asyncio
from types import SimpleNamespace

import httpx
import pytest
from agentworkflows.activities import GatewayActivities
from agentworkflows.triggers import ScheduledTrigger, poll_delay
from temporalio.testing import ActivityEnvironment


class ContinuedAsNew(Exception):
    def __init__(self, request):
        super().__init__("continue as new")
        self.request = request


def run_trigger(monkeypatch, request, results, *, patched=True, suggest_after=None, run_id="firing-run"):
    calls, delays = [], []

    async def execute(name, payload, **kwargs):
        assert name == "agentworkflows.trigger"
        calls.append(dict(payload))
        return results.pop(0)

    async def sleep(delay):
        delays.append(delay.total_seconds())

    def continue_as_new(payload):
        raise ContinuedAsNew(payload)

    info = SimpleNamespace(
        run_id=run_id,
        is_continue_as_new_suggested=lambda: suggest_after is not None and len(delays) >= suggest_after,
    )
    monkeypatch.setattr("agentworkflows.triggers.workflow.execute_activity", execute)
    monkeypatch.setattr("agentworkflows.triggers.workflow.sleep", sleep)
    monkeypatch.setattr("agentworkflows.triggers.workflow.patched", lambda name: patched)
    monkeypatch.setattr("agentworkflows.triggers.workflow.info", lambda: info)
    monkeypatch.setattr("agentworkflows.triggers.workflow.continue_as_new", continue_as_new)
    result = asyncio.run(ScheduledTrigger().run(request))
    return result, calls, delays


def test_status_reads_back_off_to_five_minutes():
    assert [poll_delay(polls) for polls in range(7)] == [30, 60, 120, 240, 300, 300, 300]


def test_trigger_waits_for_its_run_with_backoff(monkeypatch):
    results = [{"run_id": "target"}, *({"run_id": "target", "status": "running"} for _ in range(6)),
               {"run_id": "target", "status": "completed", "outcome": "published"}]
    result, calls, delays = run_trigger(monkeypatch, {"workflow": "Daily", "trigger": "daily"}, results)
    assert result == {"run_id": "target", "status": "completed", "outcome": "published"}
    assert calls[0] == {"workflow": "Daily", "trigger": "daily", "firing_id": "firing-run"}
    assert all(call["run_id"] == "target" and call["firing_id"] == "firing-run" for call in calls[1:])
    assert delays == [30, 60, 120, 240, 300, 300, 300]


def test_in_flight_trigger_keeps_its_original_schedule(monkeypatch):
    results = [
        {"run_id": "target"}, {"run_id": "target", "status": "running"}, {"run_id": "target", "status": "failed"},
    ]
    _, _, delays = run_trigger(monkeypatch, {"workflow": "Daily", "trigger": "daily"}, results, patched=False)
    assert delays == [30, 30]


def test_long_review_continues_as_new_with_the_same_firing(monkeypatch):
    results = [{"run_id": "target"}, *({"run_id": "target", "status": "running"} for _ in range(4))]
    with pytest.raises(ContinuedAsNew) as continued:
        run_trigger(monkeypatch, {"workflow": "Daily", "trigger": "daily"}, results, suggest_after=3)
    carried = continued.value.request
    assert carried == {"workflow": "Daily", "trigger": "daily", "firing_id": "firing-run", "run_id": "target",
                       "polls": 3}
    results = [{"run_id": "target", "status": "running"}, {"run_id": "target", "status": "canceled"}]
    result, calls, delays = run_trigger(monkeypatch, carried, results, run_id="continued-run")
    assert result["status"] == "canceled"
    assert calls[0] == {"workflow": "Daily", "trigger": "daily", "firing_id": "firing-run", "run_id": "target"}
    assert delays == [240]


@pytest.mark.parametrize("paused", [False, True])
def test_trigger_activity_keeps_run_content_out_of_history(monkeypatch, paused):
    requests = []

    def respond(request):
        requests.append(request)
        if paused:
            return httpx.Response(409, json={"detail": {"reason": "trigger_paused", "message": "paused"}})
        return httpx.Response(200, json={
            "run_id": "target", "status": "completed", "outcome": "published", "result": {"body": "private draft"},
            "progress": {"draft": "private draft"}, "timeline": [{"receipt": {}}], "input": {"topic": "private"},
        })

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "agentworkflows.activities.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(respond), **kw),
    )
    activity = GatewayActivities("http://gateway", "team-key").trigger
    result = asyncio.run(ActivityEnvironment().run(activity, {"run_id": "target", "workflow": "Daily"}))
    if paused:
        assert result == {"paused": True}
    else:
        assert result == {"run_id": "target", "status": "completed", "outcome": "published"}
    assert requests[0].url.params["timeline"] == "false"


def test_trigger_activity_fires_with_the_schedule_firing_id(monkeypatch):
    seen = []

    def respond(request):
        seen.append((request.url.raw_path, request.content))
        return httpx.Response(201, json={"run_id": "target", "workflow_id": "team/default/x", "request_id": "x"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "agentworkflows.activities.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(respond), **kw),
    )
    activity = GatewayActivities("http://gateway", "team-key").trigger
    result = asyncio.run(ActivityEnvironment().run(activity, {"workflow": "Daily", "trigger": "every day",
                                                             "firing_id": "f"}))
    assert result == {"run_id": "target"}
    assert seen == [(b"/v1/workflow-triggers/Daily/every%20day/fire", b'{"firing_id":"f"}')]
