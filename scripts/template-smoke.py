#!/usr/bin/env python3
"""Exercise generated gallery inputs against the Compose worker and local fixtures."""

import contextlib
import io
import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from agentworkflows import GatewayClient
from agentworkflows.cli import main as cli
from agentworkflows.scaffold import TEMPLATES


def until(client, run_id, check):
    for _ in range(60):
        run = client.run(run_id)
        assert run["status"] not in {"failed", "canceled", "timed_out", "terminated"}, run
        if check(run):
            return run
        time.sleep(1)
    raise AssertionError(f"Template did not finish: {run}")


def main():
    with (
        TemporaryDirectory() as directory,
        GatewayClient(os.environ["AGENTWORKFLOWS_URL"], api_key="local-development-only") as client,
    ):
        for template in ("code-review", "support-triage", "weekly-report", "incident-summary", "document-qa"):
            project = Path(directory) / template
            with contextlib.redirect_stdout(io.StringIO()):
                assert cli(["init", str(project), "--template", template]) == 0
            value = json.loads((project / "input.json").read_text())
            workflow = TEMPLATES[template][1]
            for approved in [True, False] if template == "code-review" else [None]:
                run_id = client.start_run(workflow, value)["run_id"]
                if approved is not None:
                    run = until(client, run_id, lambda row: row.get("progress", {}).get("stage") == "awaiting_approval")
                    assert "auth.py:10" in run["progress"]["draft"], run
                    assert not any(step["action"] == "approval" for step in run["timeline"]), run
                    client.approve_run(run_id, approved=approved)
                run = until(client, run_id, lambda row: row["status"] == "completed")
                result = run["result"]
                if template == "code-review":
                    assert result["approved"] is approved and result["reviewer"] == "demo-key", run
                elif template == "support-triage":
                    assert "priority=high" in result and "Draft reply:" in result, run
                elif template == "weekly-report":
                    assert {source["id"] for source in result["sources"]} == {"changes", "support", "incidents"}, run
                    assert all(f"[{source['id']}]" in result["report"] for source in result["sources"]), run
                elif template == "incident-summary":
                    assert len(result["logs"]["lines"]) == 4 and "[L4]" in result["summary"], run
                else:
                    assert "[S1]" in result["answer"] and result["citations"][0]["id"] == "S1", run
                    assert result["citations"][0]["url"] == "https://example.test/handbook/approvals", run
                expected_tools = {"weekly-report": 3, "incident-summary": 1, "document-qa": 1}.get(template, 0)
                assert sum(step["action"] == "tool_exec" for step in run["timeline"]) == expected_tools, run
                assert sum(step["action"] == "model_call" for step in run["timeline"]) == 1, run
                assert all(step["receipt_id"] for step in run["timeline"]), run
                assert run["budget"]["cost_usd"] > 0, run
                print(f"[templates] {template}: completed, expected evidence and receipts verified", flush=True)
        run_id = client.start_run("DocumentQAWorkflow", {"question": "What is the weather on Mars?"})["run_id"]
        run = until(client, run_id, lambda row: row["status"] == "completed")
        assert run["result"] == {"answer": "I don't know from the supplied documents.", "citations": []}, run
        assert not any(step["action"] == "model_call" for step in run["timeline"]), run
        print("[templates] document-qa: no evidence returns an abstention without a model call", flush=True)


if __name__ == "__main__":
    main()
