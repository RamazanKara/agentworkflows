#!/usr/bin/env python3
"""Run inside the Compose worker: real Temporal schedules, fake notification channels."""

import asyncio
import csv
import hashlib
import hmac
import io
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from temporalio.client import Client, ScheduleBackfill

GATEWAY = os.getenv("AGENTWORKFLOWS_URL", "http://inference-gateway:8080")
HOOK = "/v1/hooks/demo/GitHubIssueTriageWorkflow/github"
SECRET = b"compose-webhook-secret-not-for-production"


def request(method, path, body=None, *, headers=None, expected=200):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        GATEWAY + path,
        data=data,
        method=method,
        headers=headers or {"Authorization": "Bearer local-development-only", "Content-Type": "application/json"},
    )
    try:
        response = urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        result = json.load(response)
        assert response.status == expected, (response.status, result)
        return result


def hook(payload, delivery=None, timestamp=None, expected=201, github=False):
    delivery = delivery or str(uuid4())
    timestamp = str(timestamp or int(time.time()))
    body = json.dumps(payload).encode()
    if github:
        signature = hmac.new(SECRET, body, hashlib.sha256).hexdigest()
        return request(
            "POST",
            HOOK,
            payload,
            expected=expected,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Delivery": delivery,
                "X-Hub-Signature-256": "sha256=" + signature,
            },
        )
    signature = hmac.new(SECRET, f"{timestamp}.{delivery}.{HOOK}.".encode() + body, hashlib.sha256).hexdigest()
    return request(
        "POST",
        HOOK,
        payload,
        expected=expected,
        headers={
            "Content-Type": "application/json",
            "X-AW-Timestamp": timestamp,
            "X-AW-Delivery": delivery,
            "X-AW-Signature": "sha256=" + signature,
        },
    )


async def until(read, check):
    for _ in range(90):
        value = await asyncio.to_thread(read)
        if check(value):
            return value
        await asyncio.sleep(1)
    raise AssertionError(f"Timed out waiting for trigger/notification: {value}")


async def main():
    triggers = await until(
        lambda: request("GET", "/v1/workflow-triggers")["triggers"],
        lambda rows: len(rows) == 2 and not any(row.get("error") for row in rows),
    )
    assert {row["kind"] for row in triggers} == {"cron", "webhook"}
    temporal = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "temporal:7233"))
    schedule = temporal.get_schedule_handle("agentworkflows/demo/DailyReportWorkflow/daily")
    yesterday = datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=1)
    began = time.time()
    await schedule.backfill(
        ScheduleBackfill(start_at=yesterday - timedelta(minutes=1), end_at=yesterday + timedelta(minutes=1))
    )
    reports = await until(
        lambda: request("GET", "/v1/workflow-runs?workflow=DailyReportWorkflow&trigger=daily")["runs"],
        lambda rows: bool(rows) and rows[0]["created_at"] >= began,
    )
    daily_id = reports[0]["run_id"]
    assert reports[0]["trigger"] == {"name": "daily", "kind": "cron"}
    await until(
        lambda: request("GET", f"/v1/workflow-runs/{daily_id}"),
        lambda row: row.get("progress", {}).get("stage") == "awaiting_approval",
    )

    payload = {
        "action": "opened",
        "issue": {"number": 42, "title": "Sign-in regression", "body": "Local triage: " + str(uuid4())},
    }
    hook(payload, timestamp=int(time.time()) - 600, expected=401)
    pause_url = "/v1/workflow-triggers/GitHubIssueTriageWorkflow/github"
    request("PATCH", pause_url, {"paused": True})
    hook(payload, expected=409)
    request("PATCH", pause_url, {"paused": False})
    delivery = str(uuid4())
    triage_id = hook(payload, delivery, github=True)["run_id"]
    hook(payload, str(uuid4()), expected=409, github=True)
    await until(
        lambda: request("GET", f"/v1/workflow-runs/{triage_id}"),
        lambda row: row.get("progress", {}).get("stage") == "awaiting_approval",
    )
    failed_id = hook({"action": "opened"}, delivery)["run_id"]
    hook({"action": "opened"}, delivery, expected=409)
    await until(lambda: request("GET", f"/v1/workflow-runs/{failed_id}"), lambda row: row["status"] == "failed")

    budget_id = request(
        "POST",
        "/v1/workflow-runs",
        {
            "workflow": "ResearchWorkflow",
            "input": {
                "topic": "Budget notification " + "report " * 60,
                "cost_limit_usd": 2.4,
            },
        },
        expected=201,
    )["run_id"]

    for run_id, event in ((daily_id, "awaiting_approval"), (triage_id, "awaiting_approval"), (failed_id, "failed")):
        row = await until(
            lambda run_id=run_id: request("GET", f"/v1/workflow-runs/{run_id}"),
            lambda row: (
                {
                    step["receipt"].get("channel")
                    for step in row["timeline"]
                    if step["action"] == "notification" and step["receipt"].get("outcome") == "delivered"
                }
                == {"slack", "webhook", "email"}
            ),
        )
        assert any(step["action"] == "trigger" for step in row["timeline"])
        assert row["trigger"] == {
            "name": "daily" if run_id == daily_id else "github",
            "kind": "cron" if run_id == daily_id else "webhook",
        }
        assert any(step["receipt"].get("notification_event") == event for step in row["timeline"])
    with urllib.request.urlopen("http://notification-fake:8025/", timeout=10) as response:
        messages = json.load(response)["messages"]
    for run_id in (daily_id, triage_id, failed_id):
        assert {message["channel"] for message in messages if run_id in json.dumps(message)} == {
            "slack",
            "webhook",
            "email",
        }
    for run_id in (daily_id, triage_id):
        request(
            "POST",
            f"/v1/workflow-runs/{run_id}/approve",
            {"approved": False},
            headers={
                "Authorization": "Bearer demo-approver",
                "Content-Type": "application/json",
            },
        )
        row = await until(
            lambda run_id=run_id: request("GET", f"/v1/workflow-runs/{run_id}"),
            lambda row: row["status"] == "completed",
        )
        assert row["result"]["approved"] is False
    row = await until(
        lambda: request("GET", f"/v1/workflow-runs/{budget_id}"),
        lambda row: (
            {
                step["receipt"].get("channel")
                for step in row["timeline"]
                if step["receipt"].get("notification_event") == "budget_threshold"
                and step["receipt"].get("outcome") == "delivered"
            }
            == {"slack", "webhook", "email"}
        ),
    )
    if row["status"] == "running":
        request("POST", f"/v1/workflow-runs/{budget_id}/cancel")
    export = urllib.request.Request(GATEWAY + "/v1/usage/export", headers={
        "Authorization": "Bearer demo-viewer", "Accept": "text/csv",
    })
    with urllib.request.urlopen(export, timeout=20) as response:
        assert response.headers["Content-Type"].startswith("text/csv")
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
    assert rows[0]["dimension"] == "total" and rows[0]["team_id"] == "demo"
    assert {row["dimension"] for row in rows} == {"total", "provider", "workflow"}
    assert any(row["name"] == "DailyReportWorkflow" for row in rows)
    print(
        "[triggers] Temporal daily backfill, trigger history, usage CSV, GitHub signing, replay/pause protection, approval/failure/budget alerts and all three channels passed"
    )


if __name__ == "__main__":
    asyncio.run(main())
