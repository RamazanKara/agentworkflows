"""The post-install check walks a fake gateway through ready, draft, approval and evidence."""

import json
from uuid import uuid4

import agentworkflows
import httpx
import pytest
from agentworkflows import GatewayClient
from agentworkflows.acceptance import AcceptanceError, first_approved_run
from agentworkflows.cli import main

RUN = str(uuid4())
REAL_CLIENT = httpx.Client


class Gateway:
    """Just enough of the gateway's first-run API, with a polling state machine."""

    def __init__(self, **options):
        self.requests = []
        self.polls = 0
        self.approvals = []
        self.options = {
            "blockers": [], "installed": True, "simulated": True, "install_status": 200, "draft_after": 2,
            "required": 1, "approve_status": 200, "final": "published",
            "audit": {"enabled": True, "ok": True, "checked": 4},
            "audit_status": 200, "summary": True, "approval_receipt": True, "end_status": "completed", **options,
        }

    def __call__(self, request):
        self.requests.append((request.method, request.url.path))
        o = self.options
        path = request.url.path
        if path == "/v1/team/onboarding":
            sample = {"template_id": "research", "version": "0.9.0", "workflow": "ResearchWorkflow",
                      "installed": o["installed"], "ready": not o["blockers"],
                      "input": {"topic": "t", "model": "demo-openai"}}
            return httpx.Response(200, json={"providers": [], "blockers": o["blockers"], "sample": sample})
        if path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "demo-openai", "simulated": o["simulated"]}]})
        if path.endswith("/install"):
            return httpx.Response(o["install_status"], json={"detail": {"message": "Admin required."}})
        if request.method == "POST" and path == "/v1/workflow-runs":
            assert json.loads(request.content)["input"] == {"topic": "t", "model": "demo-openai"}
            return httpx.Response(201, json={"run_id": RUN})
        if path == f"/v1/workflow-runs/{RUN}/approve":
            if o["approve_status"] != 200:
                return httpx.Response(o["approve_status"], json={"detail": {"message": "Not a reviewer."}})
            self.approvals.append(request.headers["Authorization"])
            return httpx.Response(200, json={"approved": True})
        if path == f"/v1/workflow-runs/{RUN}":
            self.polls += 1
            if len(self.approvals) >= o["required"]:
                receipts = [{"action": "model_call"}] + ([{"action": "approval"}] if o["approval_receipt"] else [])
                return httpx.Response(200, json={
                    "status": o["end_status"], "result": {"status": o["final"]}, "timeline": receipts,
                    "error": {"code": "GatewayUnavailable"},
                    **({"summary": {"elapsed_seconds": 5}} if o["summary"] else {}),
                })
            stage = "awaiting_approval" if self.polls > o["draft_after"] else "researching"
            progress = {"stage": stage, "required_approvals": o["required"]}
            return httpx.Response(200, json={"status": "running", "progress": progress})
        if path == "/v1/team/audit/verify":
            return httpx.Response(o["audit_status"], json=o["audit"])
        raise AssertionError(f"unexpected {request.method} {path}")


class Time:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def client(monkeypatch, gateway, key="admin-key"):
    monkeypatch.setattr(
        agentworkflows.httpx, "Client",
        lambda *args, **kwargs: REAL_CLIENT(*args, transport=httpx.MockTransport(gateway), **kwargs),
    )
    return GatewayClient("http://gateway.test", api_key=key)


def run_check(monkeypatch, gateway, approvers=(), **kwargs):
    ticking = Time()
    with client(monkeypatch, gateway) as admin:
        reviewers = [client(monkeypatch, gateway, key=name) for name in approvers]
        return first_approved_run(
            admin, approvers=reviewers, clock=ticking.clock, sleep=ticking.sleep, poll_seconds=2.0, **kwargs
        )


def failure(monkeypatch, gateway, **kwargs):
    with pytest.raises(AcceptanceError) as error:
        run_check(monkeypatch, gateway, **kwargs)
    return error.value


def test_a_ready_install_completes_every_stage_with_timings(monkeypatch):
    gateway = Gateway()
    shown = []
    result = run_check(monkeypatch, gateway, progress=shown.append)
    assert result["ok"] is True and result["run_id"] == RUN and result["deadline_seconds"] == 300
    assert [row["stage"] for row in result["stages"]] == [
        "ready", "installed", "started", "drafted", "approved", "completed", "evidence", "audit",
    ]
    assert shown == result["stages"]
    seconds = [row["seconds"] for row in result["stages"]]
    assert seconds == sorted(seconds) and result["total_seconds"] == seconds[-1] > 0
    assert result["stages"][0]["detail"].endswith("(simulated)")
    assert result["stages"][-1]["detail"] == "Verified 4 events" and result["notes"] == []
    assert ("POST", "/v1/workflow-runs") in gateway.requests and gateway.approvals == ["Bearer admin-key"]


def test_blockers_stop_before_any_run_and_repeat_what_the_console_says(monkeypatch):
    gateway = Gateway(blockers=["Connect a provider for an approved Research model, then refresh readiness."])
    error = failure(monkeypatch, gateway)
    assert error.stage == "ready" and "Connect a provider" in str(error) and error.stages == []
    assert ("POST", "/v1/workflow-runs") not in gateway.requests


def test_a_real_provider_needs_explicit_permission_to_spend(monkeypatch):
    gateway = Gateway(simulated=False)
    error = failure(monkeypatch, gateway)
    assert error.stage == "ready" and "--allow-paid" in str(error) and "demo-openai" in str(error)
    assert ("POST", "/v1/workflow-runs") not in gateway.requests
    result = run_check(monkeypatch, Gateway(simulated=False), allow_paid=True)
    assert result["stages"][0]["detail"].endswith("(real provider)")


@pytest.mark.parametrize("allowed", [True, False])
def test_the_sample_is_installed_only_when_the_credential_may(monkeypatch, allowed):
    gateway = Gateway(installed=False, install_status=200 if allowed else 403)
    if allowed:
        assert run_check(monkeypatch, gateway)["stages"][1]["detail"] == "Installed research 0.9.0"
    else:
        error = failure(monkeypatch, gateway)
        assert error.stage == "installed" and "admin" in str(error).lower()
        assert ("POST", "/v1/workflow-runs") not in gateway.requests


def test_a_quorum_needs_distinct_reviewers_and_uses_each_credential(monkeypatch):
    short = Gateway(required=2)
    error = failure(monkeypatch, short)
    assert error.stage == "approved" and "2 distinct reviewers" in str(error) and "--approver-key-env" in str(error)
    assert short.approvals == []
    shared = Gateway(required=2)
    result = run_check(monkeypatch, shared, approvers=("second-reviewer",))
    assert shared.approvals == ["Bearer admin-key", "Bearer second-reviewer"]
    assert result["stages"][3]["detail"] == "Awaiting 2 approvals"


def test_each_failure_names_its_stage_and_what_to_do(monkeypatch):
    denied = failure(monkeypatch, Gateway(approve_status=403))
    assert denied.stage == "approved" and "admin or approver role" in str(denied)
    ended = failure(monkeypatch, Gateway(end_status="failed", required=0))
    assert ended.stage == "drafted" and "GatewayUnavailable" in str(ended) and "worker logs" in str(ended)
    skipped = failure(monkeypatch, Gateway(required=0))
    assert skipped.stage == "drafted" and "without pausing for approval" in str(skipped)
    late = failure(monkeypatch, Gateway(draft_after=10_000), deadline=20)
    assert late.stage == "drafted" and "Timed out after 20 seconds" in str(late)
    assert [row["stage"] for row in late.stages] == ["ready", "installed", "started"]
    assert failure(monkeypatch, Gateway(final="rejected")).stage == "completed"
    assert "outcome rejected" in str(failure(monkeypatch, Gateway(final="rejected")))
    assert failure(monkeypatch, Gateway(approval_receipt=False)).stage == "evidence"


def test_the_budget_is_enforced_even_when_the_run_succeeds(monkeypatch):
    error = failure(monkeypatch, Gateway(draft_after=2), deadline=3.5)
    assert error.stage == "completed" and "took 4 seconds, over the 3.5-second budget" in str(error)
    assert run_check(monkeypatch, Gateway(draft_after=2), deadline=4)["total_seconds"] == 4


@pytest.mark.parametrize(
    ("options", "stage", "note"),
    [
        ({"audit_status": 403}, "audit", "needs a team admin credential"),
        ({"audit": {"enabled": False}}, "audit", "audit chain is disabled"),
        ({"summary": False}, "evidence", "predates run insights"),
    ],
)
def test_optional_evidence_is_reported_as_a_note_not_a_failure(monkeypatch, options, stage, note):
    result = run_check(monkeypatch, Gateway(**options))
    assert any(note in text for text in result["notes"]) and result["ok"] is True
    assert result["stages"][-1]["stage"] == "audit" and stage in {row["stage"] for row in result["stages"]}


def test_a_broken_audit_chain_fails_the_check(monkeypatch):
    broken = {"enabled": True, "ok": False, "checked": 4, "first_break": {"sequence": 3}}
    error = failure(monkeypatch, Gateway(audit=broken))
    assert error.stage == "audit" and "did not verify" in str(error)
    assert failure(monkeypatch, Gateway(audit={"enabled": True, "ok": True, "checked": 0})).stage == "audit"


def patch_cli(monkeypatch, gateway):
    monkeypatch.setattr(
        agentworkflows.httpx, "Client", lambda **kwargs: REAL_CLIENT(**kwargs, transport=httpx.MockTransport(gateway))
    )
    monkeypatch.setattr("agentworkflows.acceptance.time.sleep", lambda seconds: None)


def test_cli_prints_each_stage_and_a_link_to_the_run(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    monkeypatch.setenv("AGENTWORKFLOWS_URL", "http://gateway.test")
    monkeypatch.setenv("SECOND_KEY", "second-reviewer")
    gateway = Gateway(required=2)
    patch_cli(monkeypatch, gateway)
    assert main(["check", "--approver-key-env", "SECOND_KEY"]) == 0
    out = capsys.readouterr().out
    for stage in ("ready", "installed", "started", "drafted", "approved", "completed", "evidence", "audit"):
        assert f"  ok  {stage}" in out
    assert "First approved run completed in" in out and f"/console/#run/{RUN}" in out
    assert "admin-key" not in out and "second-reviewer" not in out


def test_cli_json_and_failures_are_machine_readable(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    monkeypatch.setenv("AGENTWORKFLOWS_URL", "http://gateway.test")
    patch_cli(monkeypatch, Gateway())
    assert main(["check", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    patch_cli(monkeypatch, Gateway(blockers=["Raise the zero budget in Team settings before starting the sample."]))
    assert main(["check", "--json"]) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False and body["stage"] == "ready" and "zero budget" in body["message"]
    assert main(["check"]) == 1
    captured = capsys.readouterr()
    assert "Check failed at ready" in captured.err and "admin-key" not in captured.err + captured.out


def test_cli_refuses_missing_reviewer_keys_before_calling_the_gateway(monkeypatch, capsys):
    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "admin-key")
    monkeypatch.delenv("MISSING_REVIEWER", raising=False)
    gateway = Gateway()
    patch_cli(monkeypatch, gateway)
    with pytest.raises(SystemExit) as error:
        main(["check", "--approver-key-env", "MISSING_REVIEWER"])
    assert error.value.code == 2 and "MISSING_REVIEWER" in capsys.readouterr().err and gateway.requests == []
    with pytest.raises(SystemExit):
        main(["check", "--approver-key", "inline-secret"])
