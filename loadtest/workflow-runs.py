#!/usr/bin/env python3
"""Bounded concurrent, fixture-only workflow runs against the existing Compose trial."""

import concurrent.futures
import json
import os
import statistics
import time
import urllib.request
from pathlib import Path

BASE = os.getenv("AGENTWORKFLOWS_GATEWAY_URL", "http://127.0.0.1:8080")


def request(path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body else None,
        headers={"X-API-Key": "demo-builder", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def one_run(index):
    start = time.monotonic()
    run = request(
        "/v1/workflow-runs",
        {
            "workflow": "SupportTriageWorkflow",
            "input": {"ticket": f"Load probe {index}: I cannot sign in", "model": "demo-openai"},
        },
    )
    for _ in range(120):
        detail = request(f"/v1/workflow-runs/{run['run_id']}")
        if detail["status"] == "completed":
            rows = [row for row in detail["timeline"] if row["action"] == "model_call"]
            assert len(rows) == 1 and rows[0]["receipt_id"] and rows[0]["status_code"] == 200
            assert detail["budget"]["tokens"] == 7 and detail["budget"]["cost_usd"] == 0.011
            return time.monotonic() - start
        assert detail["status"] == "running", detail["status"]
        time.sleep(0.25)
    raise TimeoutError(f"Run {run['run_id']} did not finish")


def main():
    # Require the fixture route before creating runs; no provider keys are read by this script.
    models = request("/v1/models")["data"]
    assert any(model["id"] == "demo-openai" for model in models), "Use the unmodified fake Compose stack"
    started = time.monotonic()
    before = request("/v1/usage")["spend"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        durations = list(pool.map(one_run, range(20)))
    elapsed = time.monotonic() - started
    after = request("/v1/usage")["spend"]
    delta = after["reserved_and_spent_usd"] - before["reserved_and_spent_usd"]
    assert abs(delta - 20 * 0.011) < 1e-8, "Shared team accounting lost or duplicated charges"
    report = {
        "runs": 20,
        "concurrency": 2,
        "failures": 0,
        "elapsed_seconds": round(elapsed, 3),
        "runs_per_second": round(20 / elapsed, 3),
        "p50_seconds": round(statistics.median(durations), 3),
        "p95_seconds": round(sorted(durations)[18], 3),
        "cost_usd": round(delta, 3),
        "provider": "local protocol fixture; not provider latency or a capacity claim",
    }
    out = Path("results/loadtest/workflow-runs.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(out.read_text())


if __name__ == "__main__":
    main()
