import argparse
import json

import agentworkflows
import httpx
import pytest
from agentworkflows import GatewayClient, GatewayError
from agentworkflows.cli import duration, main
from temporalio import workflow


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        agentworkflows.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler))
    )


@workflow.defn(name="ReviewedRelease")
class _Release:
    @workflow.run
    async def run(self) -> str:
        return "ok"


def test_client_configures_escalation_at_a_reviewed_revision(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"revision": 4, "escalations": [], "available_channels": ["slack"]})
        if request.method == "PUT":
            return httpx.Response(200, json={"revision": 5, "escalation": {"workflow": "ReviewedRelease"}})
        return httpx.Response(200, json={"revision": 6, "removed": "ReviewedRelease"})

    transport(monkeypatch, handler)
    with GatewayClient("http://gateway.test", api_key="admin-key") as client:
        saved = client.set_approval_escalation(
            _Release, after_seconds=3600, approver_role="approver", reviewers=["maya"], channels=["slack"]
        )
        client.set_approval_escalation("Plain Name", after_seconds=600, revision="9")
        removed = client.remove_approval_escalation(_Release, revision=5)
    assert saved["revision"] == 5 and removed["removed"] == "ReviewedRelease"
    assert [(r.method, r.url.raw_path) for r in requests] == [
        ("GET", b"/v1/team/approval-escalations"),
        ("PUT", b"/v1/team/workflows/ReviewedRelease/approval-escalation"),
        ("PUT", b"/v1/team/workflows/Plain%20Name/approval-escalation"),
        ("DELETE", b"/v1/team/workflows/ReviewedRelease/approval-escalation"),
    ]
    assert requests[1].headers["If-Match"] == "4" and json.loads(requests[1].content) == {
        "after_seconds": 3600, "approver_role": "approver", "reviewers": ["maya"], "channels": ["slack"],
    }
    assert requests[2].headers["If-Match"] == "9" and json.loads(requests[2].content) == {"after_seconds": 600}
    assert requests[3].headers["If-Match"] == "5"


def test_escalation_writes_are_not_repeated_after_ambiguous_failures(monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(502)

    transport(monkeypatch, handler)
    with GatewayClient("http://gateway.test", api_key="admin-key") as client:
        monkeypatch.setattr(client, "_sleep", lambda _: None)
        with pytest.raises(GatewayError):
            client.set_approval_escalation("Plain", after_seconds=600, revision=1)
    assert len(attempts) == 1


def test_review_queue_follows_cursors_and_keeps_runs_you_may_review(monkeypatch):
    pages = {
        None: {"runs": [
            {"run_id": "a", "approval_access": {"eligible": True, "via": "role", "eligible_at": None}},
            {"run_id": "b", "approval_access": {"eligible": False, "via": None, "eligible_at": 10.0}},
        ], "next_offset": 2, "next_cursor": "page-2"},
        "page-2": {"runs": [], "next_offset": None, "next_cursor": "page-3"},
        "page-3": {"runs": [{"run_id": "c"}], "next_offset": None, "next_cursor": None},
    }
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])

    transport(monkeypatch, handler)
    with GatewayClient("http://gateway.test", api_key="approver-key") as client:
        assert [run["run_id"] for run in client.review_queue(workflow="Release")] == ["a", "c"]
    assert seen[0] == {"offset": "0", "status": "awaiting_approval", "limit": "100", "workflow": "Release"}
    assert seen[2]["cursor"] == "page-3" and "project" not in seen[2]


@pytest.mark.parametrize(("text", "seconds"), [("90", 90), ("90s", 90), ("45m", 2700), ("2H", 7200), ("1d", 86400)])
def test_durations_accept_common_units(text, seconds):
    assert duration(text) == seconds


@pytest.mark.parametrize("text", ["", "h", "1.5h", "-5m", "2w", "ten"])
def test_durations_reject_ambiguous_input(text):
    with pytest.raises(argparse.ArgumentTypeError, match="45m"):
        duration(text)


def test_escalation_commands(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path, request.headers.get("If-Match"),
                      json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={"revision": 3, "escalations": []})

    transport(monkeypatch, respond)
    assert main(["workflows", "escalation", "list"]) == 0
    assert main([
        "workflows", "escalation", "set", "Release", "--after", "4h", "--all-approvers",
        "--reviewer", "maya", "--reviewer", "leo", "--channel", "slack", "--revision", "3",
    ]) == 0
    assert main(["workflows", "escalation", "set", "Release", "--after", "90m", "--no-notify", "--revision", "3"]) == 0
    assert main(["workflows", "escalation", "remove", "Release", "--revision", "3"]) == 0
    assert main(["workflows", "register", "Quick", "--model", "m", "--approver-role", "admin",
                 "--approval-timeout", "2d", "--revision", "3"]) == 0
    path = "/v1/team/workflows/Release/approval-escalation"
    assert calls[0][:2] == ("GET", "/v1/team/approval-escalations")
    assert calls[1] == ("PUT", path, "3", {
        "after_seconds": 14400, "reviewers": ["maya", "leo"], "approver_role": "approver", "channels": ["slack"],
    })
    assert calls[2] == ("PUT", path, "3", {"after_seconds": 5400, "reviewers": [], "channels": []})
    assert calls[3] == ("DELETE", path, "3", None)
    assert calls[4][3] == {"allowed_models": ["m"], "approver_role": "admin", "approval_timeout_seconds": 172800}
    with pytest.raises(SystemExit):
        main(["workflows", "escalation", "set", "Release", "--after", "1h", "--no-notify", "--channel", "slack"])
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize(("reason", "hint"), [
    ("approval_escalation_conflict", "escalation list"),
    ("approval_escalation_invalid", "approval timeout"),
])
def test_escalation_errors_are_actionable(monkeypatch, capsys, reason, hint):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    transport(monkeypatch, lambda request: httpx.Response(
        409 if reason.endswith("conflict") else 422, json={"detail": {"reason": reason, "message": "No."}}
    ))
    assert main(["workflows", "escalation", "set", "Release", "--after", "1h", "--revision", "1"]) == 1
    assert hint in capsys.readouterr().err


def test_pending_lists_reviews_you_may_give(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "approver-key")
    run = {
        "run_id": "4a5e3c5e-8f5e-4f8a-9c5e-0d4d1e2f3a4b", "workflow": "Release", "project": "default",
        "created_at": 1.0, "progress": {"stage": "awaiting_approval", "draft": "private draft", "approved_by": ["maya"],
                                        "required_approvals": 2, "expires_at": "2026-10-20T00:00:00+00:00"},
        "approval_escalation": {"escalated": True},
        "approval_access": {"eligible": True, "via": "escalation", "eligible_at": None},
    }
    blocked = {**run, "run_id": "other", "approval_access": {"eligible": False, "via": None, "eligible_at": 5.0}}
    transport(monkeypatch, lambda request: httpx.Response(
        200, json={"runs": [run, blocked], "next_offset": None, "next_cursor": None}
    ))
    assert main(["runs", "pending", "--workflow", "Release"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows == [{
        "run_id": run["run_id"], "workflow": "Release", "project": "default", "created_at": 1.0,
        "approved_by": ["maya"], "required_approvals": 2, "expires_at": "2026-10-20T00:00:00+00:00",
        "eligible_via": "escalation", "escalated": True,
    }]


def test_denied_vote_points_at_the_escalation(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "approver-key")
    transport(monkeypatch, lambda request: httpx.Response(403, json={"detail": {
        "reason": "team_role_required", "message": "Use admin. This review opens to you at 2026-10-14T12:00:00+00:00.",
    }}))
    assert main(["runs", "approve", "4a5e3c5e-8f5e-4f8a-9c5e-0d4d1e2f3a4b"]) == 1
    err = capsys.readouterr().err
    assert "opens to you at" in err and "approval_escalation" in err
