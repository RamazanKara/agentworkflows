import importlib.util
import time
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "first_approved_run",
    Path(__file__).resolve().parents[3] / "scripts/first-approved-run.py",
)
quickstart = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quickstart)


def test_script_installs_approves_and_checks_receipts(monkeypatch):
    calls = []
    approved = False

    def api(url, key, path, *, body=None):
        nonlocal approved
        calls.append((path, body))
        if path == "/v1/team":
            return {"team_id": "team"}
        if path == "/v1/models":
            return {"data": [{"id": "trial", "simulated": True}]}
        if path == "/v1/workflow-templates":
            return [{"id": "research", "version": "0.9.0", "installable": True}]
        if path.endswith("/install"):
            return {}
        if path == "/v1/workflow-runs":
            return {"run_id": "run"}
        if path.endswith("/approve"):
            approved = True
            return {}
        if path.endswith("/verify"):
            return {"ok": True, "checked": 5}
        return {
            "status": "completed" if approved else "running",
            "progress": {"stage": "awaiting_approval", "draft": "A simulated draft."},
            "result": {"status": "published"},
            "timeline": [{"action": "approval"}],
        }

    monkeypatch.setattr(quickstart, "api", api)
    monkeypatch.setattr(quickstart.time, "sleep", lambda seconds: None)
    assert quickstart.first_run("http://trial", "key", time.monotonic() + 10) == "run"
    assert ("/v1/workflow-templates/research/install", {"version": "0.9.0"}) in calls
    assert ("/v1/workflow-runs/run/approve", {"approved": True}) in calls
    assert calls[-1][0] == "/v1/team/audit/verify"


def test_script_refuses_real_models_and_expired_deadline(monkeypatch):
    monkeypatch.setattr(quickstart, "api", lambda *args: {"data": [{"id": "real"}]})
    with pytest.raises(RuntimeError, match="simulated trial"):
        quickstart.first_run("http://trial", "key", time.monotonic() + 10)
    with pytest.raises(TimeoutError, match="five-minute budget"):
        quickstart.first_run("http://trial", "key", time.monotonic() - 1)
