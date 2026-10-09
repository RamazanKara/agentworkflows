#!/usr/bin/env python3
"""Quiesced Compose backups and isolated 0.2.0 upgrade/restore drills; no cloud calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_RELEASE = "f17850aa91091c306921db577c18c15df7da3ef0"
DATABASES = ("temporal", "temporal_visibility")
FILES = ("temporal.dump", "temporal_visibility.dump", "gateway.rdb", "receipts.jsonl", "anchor.json")
SERVICES = ("inference-gateway", "workflow-worker", "temporal")


def run(*args, data=None):
    result = subprocess.run(args, input=data, capture_output=True, cwd=ROOT)
    if result.returncode:
        raise RuntimeError((result.stderr + result.stdout).decode(errors="replace")[-6000:])
    return result.stdout


def docker_path(path):
    path = Path(path).resolve().as_posix()
    return f"/mnt/{path[0].lower()}{path[2:]}" if os.name == "nt" else path


def compose_command(project, root=ROOT, override=None):
    command = ["docker"]
    if os.name == "nt":
        ports = [f"AGENTWORKFLOWS_{name}_PORT" for name in ("GATEWAY", "RAG", "TEMPORAL", "TEMPORAL_UI")]
        command = ["wsl.exe", "-d", "Ubuntu", "-e", "env"]
        command += [f"{key}={os.environ[key]}" for key in ports if key in os.environ]
        command += ["docker"]
    command += ["compose", "-p", project, "-f", docker_path(root / "deploy/compose/compose.yaml")]
    if override:
        command += ["-f", docker_path(override)]
    return command


def verify_receipts(directory):
    anchor = json.loads((directory / "anchor.json").read_text())
    if not anchor.get("chains"):
        raise ValueError("Backup contains no receipt chains")
    run(
        sys.executable,
        str(ROOT / "scripts/audit-verify.py"),
        str(directory / "receipts.jsonl"),
        "--anchor",
        str(directory / "anchor.json"),
        "--strict-continuity",
    )


def verify_backup(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("version") != 1 or set(manifest["sha256"]) != set(FILES):
        raise ValueError("Unsupported or incomplete backup manifest")
    for name in FILES:
        content = (directory / name).read_bytes()
        if not content:
            raise ValueError(f"Backup file is empty: {name}")
        if hashlib.sha256(content).hexdigest() != manifest["sha256"][name]:
            raise ValueError(f"Backup checksum mismatch: {name}")
    verify_receipts(directory)


def backup(compose, directory, receipts=None):
    directory.mkdir(parents=True, exist_ok=False)
    try:
        # Stop consumers before ingress, then Temporal, to capture one application recovery point.
        run(*compose, "stop", "-t", "210", "workflow-worker")
        run(*compose, "stop", "-t", "210", "inference-gateway")
        run(*compose, "stop", "-t", "60", "temporal")
        log = receipts.read_bytes() + b"\n" if receipts else b""
        log += run(*compose, "logs", "--no-color", "--no-log-prefix", "inference-gateway")
        (directory / "receipts.jsonl").write_bytes(log)
        run(
            sys.executable,
            str(ROOT / "scripts/audit-anchor.py"),
            str(directory / "receipts.jsonl"),
            "--output",
            str(directory / "anchor.json"),
        )
        verify_receipts(directory)
        for db in DATABASES:
            (directory / f"{db}.dump").write_bytes(
                run(*compose, "exec", "-T", "temporal-postgres", "pg_dump", "-U", "temporal", "-Fc", db)
            )
        run(*compose, "exec", "-T", "budget-redis", "redis-cli", "SAVE")
        (directory / "gateway.rdb").write_bytes(run(*compose, "exec", "-T", "budget-redis", "cat", "/data/dump.rdb"))
        manifest = {
            "version": 1,
            "created_at": time.time(),
            "sha256": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in FILES},
        }
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        verify_backup(directory)
    finally:
        run(*compose, "up", "-d", "--wait", "--wait-timeout", "180", *SERVICES)


def restore(compose, directory):
    verify_backup(directory)
    running = run(*compose, "ps", "--services", "--status", "running").decode().splitlines()
    if set(running) & set(SERVICES):
        raise ValueError("Stop gateway, workers and Temporal before restoring; use a new isolated project")
    run(*compose, "up", "-d", "--wait", "temporal-postgres", "budget-redis")
    keyspace = run(*compose, "exec", "-T", "budget-redis", "redis-cli", "INFO", "keyspace").decode()
    databases = run(
        *compose,
        "exec",
        "-T",
        "temporal-postgres",
        "psql",
        "-U",
        "temporal",
        "-d",
        "postgres",
        "-Atc",
        "SELECT datname FROM pg_database WHERE datname IN ('temporal','temporal_visibility')",
    )
    existing = set(databases.decode().splitlines())
    if any(line.startswith("db") for line in keyspace.splitlines()):
        raise ValueError("Restore requires empty Redis; use a NEW Compose project")
    for db in existing:
        tables = run(
            *compose,
            "exec",
            "-T",
            "temporal-postgres",
            "psql",
            "-U",
            "temporal",
            "-d",
            db,
            "-Atc",
            "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')",
        )
        if tables.strip() != b"0":
            raise ValueError("Restore requires empty Temporal databases; use a NEW Compose project")
    for db in DATABASES:
        if db not in existing:
            run(*compose, "exec", "-T", "temporal-postgres", "createdb", "-U", "temporal", db)
        run(
            *compose,
            "exec",
            "-T",
            "temporal-postgres",
            "pg_restore",
            "-U",
            "temporal",
            "-d",
            db,
            "--exit-on-error",
            data=(directory / f"{db}.dump").read_bytes(),
        )
    run(*compose, "stop", "budget-redis")
    # The empty target's AOF would otherwise take precedence over the restored RDB.
    run(
        *compose,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--user",
        "0",
        "--entrypoint",
        "sh",
        "budget-redis",
        "-ec",
        "cat > /data/dump.rdb; rm -rf /data/appendonlydir; chown redis:redis /data/dump.rdb",
        data=(directory / "gateway.rdb").read_bytes(),
    )
    # Load the RDB with AOF disabled, then generate a fresh AOF before the normal service starts.
    run(
        *compose,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--user",
        "redis",
        "--entrypoint",
        "sh",
        "budget-redis",
        "-ec",
        """
redis-server --appendonly no --daemonize yes
trap 'redis-cli shutdown nosave >/dev/null 2>&1 || true' EXIT
redis-cli CONFIG SET appendonly yes
for attempt in $(seq 1 60); do
  info="$(redis-cli INFO persistence)"
  if echo "$info" | grep -q 'aof_rewrite_in_progress:0' && echo "$info" | grep -q 'aof_last_bgrewrite_status:ok'; then
    redis-cli shutdown save
    exit 0
  fi
  sleep 1
done
exit 1
""",
    )
    run(*compose, "up", "-d", "--wait", "--wait-timeout", "180", *SERVICES)
    restored = directory / "restored-receipts.jsonl"
    restored.write_bytes(
        (directory / "receipts.jsonl").read_bytes()
        + b"\n"
        + run(*compose, "logs", "--no-color", "--no-log-prefix", "inference-gateway")
    )
    run(
        sys.executable,
        str(ROOT / "scripts/audit-verify.py"),
        str(restored),
        "--anchor",
        str(directory / "anchor.json"),
        "--strict-continuity",
    )


def request(path, body=None, key="demo-builder"):
    url = f"http://127.0.0.1:{os.environ['AGENTWORKFLOWS_GATEWAY_PORT']}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-API-Key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        exc.msg = exc.read().decode(errors="replace")
        raise


def wait_run(run_id, stage):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            result = request(f"/v1/workflow-runs/{run_id}")
        except HTTPError as exc:
            if exc.code != 503:
                raise
            time.sleep(1)
            continue
        if result["status"] == stage or result.get("progress", {}).get("stage") == stage:
            return result
        if result["status"] in {"failed", "terminated", "timed_out", "canceled"}:
            raise AssertionError(f"Run {run_id} ended as {result['status']}")
        time.sleep(1)
    raise TimeoutError(f"Run {run_id} did not reach {stage}")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return str(sock.getsockname()[1])


def state(compose):
    # DUMP preserves value encodings; TTLs are checked separately by the unit/integration assertions.
    code = """
import hashlib, json, os, redis
r = redis.Redis.from_url(os.environ['SANDBOX_BUDGET_REDIS_URL'])
print(json.dumps({k.decode(): hashlib.sha256(r.dump(k)).hexdigest() for k in sorted(r.scan_iter())
                  if b'audit-chain' not in k and not k.endswith(b':schema-version')}))
"""
    return json.loads(run(*compose, "run", "--rm", "--no-deps", "-T", "inference-gateway", "python", "-c", code))


def schema_versions(compose):
    versions = {}
    for db in DATABASES:
        versions[db] = (
            run(
                *compose,
                "exec",
                "-T",
                "temporal-postgres",
                "psql",
                "-U",
                "temporal",
                "-d",
                db,
                "-Atc",
                "SELECT curr_version FROM schema_version",
            )
            .decode()
            .strip()
        )
        if not versions[db]:
            raise AssertionError(f"Missing versioned schema in {db}")
    return versions


def check_migrations(compose):
    code = """
import os, redis
from app.state_migrations import SCHEMA_VERSION, migrate
r = redis.Redis.from_url(os.environ['SANDBOX_BUDGET_REDIS_URL'], decode_responses=True)
prefix = 'hardening-migration-probe'
r.set(prefix + ':retained', 'receipt', ex=600)
try:
    migrate(r, prefix)
    migrate(r, prefix)
    assert r.get(prefix + ':retained') == 'receipt' and 590 <= r.ttl(prefix + ':retained') <= 600
    future = str(SCHEMA_VERSION + 1)
    r.set(prefix + ':schema-version', future)
    try:
        migrate(r, prefix)
    except (redis.ResponseError, RuntimeError):
        pass
    else:
        raise AssertionError('Accepted a newer schema')
    assert r.get(prefix + ':schema-version') == future
finally:
    r.delete(prefix + ':schema-version', prefix + ':retained')
"""
    run(*compose, "exec", "-T", "inference-gateway", "python", "-c", code)


def check_shutdown(compose):
    def count():
        code = "import json,urllib.request; print(json.load(urllib.request.urlopen('http://cloud-fake:8000/stats')).get('openai',0))"
        return int(run(*compose, "exec", "-T", "cloud-fake", "python", "-c", code))

    before = count()
    run_id = request(
        "/v1/workflow-runs",
        {"workflow": "SupportTriageWorkflow", "input": {"ticket": "hardening slow step", "model": "demo-openai"}},
    )["run_id"]
    for _ in range(30):
        if count() > before:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("Slow activity never started")
    run(*compose, "stop", "-t", "210", "workflow-worker")
    stopped = json.loads(run(*compose, "ps", "--all", "--format", "json", "workflow-worker"))
    assert stopped["ExitCode"] == 0, "Worker did not drain before its termination deadline"
    # The activity completion schedules a final workflow task, which a replacement worker consumes.
    run(*compose, "up", "-d", "--wait", "workflow-worker")
    result = wait_run(run_id, "completed")
    assert len(result["timeline"]) == 1 and result["budget"]["tokens"] == 7
    assert count() == before + 1, "Graceful termination repeated a provider call"


def check_dependency_outages(compose):
    results = []
    for dependency in ("budget-redis", "temporal", "temporal-postgres"):
        for fault in ("stop", "pause"):
            body = {
                "workflow": "SupportTriageWorkflow", "request_id": str(uuid4()),
                "input": {"ticket": "Dependency recovery probe", "model": "demo-openai"},
            }
            run(*compose, fault, dependency)
            started = time.monotonic()
            try:
                assert request("/healthz")["status"] == "ok", "Dependency failure broke liveness"
                try:
                    request("/v1/workflow-runs", body)
                except HTTPError as exc:
                    assert exc.code == 503, f"{dependency}/{fault}: expected retryable 503, got {exc.code}"
                else:
                    raise AssertionError(f"{dependency}/{fault}: unavailable dependency accepted a new run")
                elapsed = time.monotonic() - started
                assert elapsed < 30, f"{dependency}/{fault}: failure exceeded the HTTP deadline"
            finally:
                run(*compose, "unpause" if fault == "pause" else "start", dependency)
                run(*compose, "up", "-d", "--wait", "--wait-timeout", "180", *SERVICES)
            recovered = request("/v1/workflow-runs", body)
            assert request("/v1/workflow-runs", body)["run_id"] == recovered["run_id"]
            finished = wait_run(recovered["run_id"], "completed")
            models = [row for row in finished["timeline"] if row["action"] == "model_call"]
            assert len(models) == 1 and models[0]["status_code"] == 200 and models[0]["receipt_id"]
            assert finished["budget"]["tokens"] == 7 and finished["budget"]["cost_usd"] == 0.011
            results.append({"dependency": dependency, "fault": fault, "failure_seconds": round(elapsed, 3),
                            "run_id": recovered["run_id"], "model_receipts": 1})
    return results


def drill(upgrade):
    project = "aw-hardening-" + uuid4().hex[:8]
    out = ROOT / ".out" / project
    out.mkdir(parents=True)
    for name in ("GATEWAY", "RAG", "TEMPORAL", "TEMPORAL_UI"):
        os.environ[f"AGENTWORKFLOWS_{name}_PORT"] = free_port()
    previous = out / "previous"
    if upgrade:
        previous.mkdir()
        archive = out / "previous.tar"
        run("git", "archive", "--format=tar", "--output", str(archive), PREVIOUS_RELEASE)
        with tarfile.open(archive) as tar:
            tar.extractall(previous, filter="data")
    override = out / "images.json"

    def images(version):
        override.write_text(
            json.dumps(
                {
                    "services": {
                        "inference-gateway": {"image": f"agentworkflows/{project}-gateway:{version}"},
                        "cloud-fake": {"image": f"agentworkflows/{project}-gateway:{version}"},
                        "workflow-worker": {"image": f"agentworkflows/{project}-worker:{version}"},
                    }
                }
            )
        )

    images("previous" if upgrade else "current")
    old = compose_command(project, previous if upgrade else ROOT, override)
    current = compose_command(project, ROOT, override)
    try:
        for service in ("inference-gateway", "workflow-worker"):
            print(f"[hardening] building {service} ({'0.2.0' if upgrade else 'current'})", flush=True)
            run(*old, "build", service)
        run(*old, "up", "-d", "--wait", "--wait-timeout", "180", *SERVICES)
        started = request("/v1/workflow-runs", {"input": {"topic": "Hardening recovery probe"}})
        run_id = started["run_id"]
        before = wait_run(run_id, "awaiting_approval")
        usage = request("/v1/usage")
        snapshot = state(old)
        (out / "before-state.json").write_text(json.dumps(snapshot, indent=2) + "\n")
        versions = schema_versions(old)
        backup(old, out / "backup")
        if upgrade:
            print("[hardening] upgrading existing volumes to current images", flush=True)
            run(*old, "stop", "-t", "210", "workflow-worker", "inference-gateway")
            images("current")
            for service in ("inference-gateway", "workflow-worker"):
                run(*current, "build", service)
            run(*current, "up", "-d", "--wait", "--wait-timeout", "180", *SERVICES)
        else:
            print("[hardening] deleting ONLY drill volumes and restoring", flush=True)
            run(*current, "down", "-v")
            restore(current, out / "backup")
        restored_state = state(current)
        (out / "after-state.json").write_text(json.dumps(restored_state, indent=2) + "\n")
        assert restored_state == snapshot, "Gateway keys or budgets changed"
        assert schema_versions(current) == versions, "Temporal schema versions changed unexpectedly"
        after = wait_run(run_id, "awaiting_approval")
        assert after["budget"] == before["budget"] and after["timeline"] == before["timeline"]
        assert request("/v1/usage") == usage, "Team budgets changed"
        assert run_id in [r["run_id"] for r in request("/v1/workflow-runs")["runs"]]
        request(f"/v1/workflow-runs/{run_id}/approve", {"approved": True}, key="demo-approver")
        finished = wait_run(run_id, "completed")
        assert finished["result"]["status"] == "published"
        models = [row for row in finished["timeline"] if row["action"] == "model_call"]
        assert len(models) == 2, "Upgrade/restore replayed a completed model step"
        check_migrations(current)
        check_shutdown(current)
        dependency_faults = [] if upgrade else check_dependency_outages(current)
        # Re-backup also checks the restored/new chain against all pre-upgrade lifetimes.
        backup(current, out / "verified", out / "backup/receipts.jsonl")
        report = {
            "status": "pass",
            "scenario": "upgrade" if upgrade else "restore",
            "run_id": run_id,
            "previous_release": PREVIOUS_RELEASE if upgrade else None,
            "schema_versions": versions,
            "gateway_keys_preserved": len(snapshot),
            "receipt_chain_verified": True,
            "graceful_inflight_step": True,
            "migration_replay_and_future_rejection": True,
            "dependency_faults": dependency_faults,
        }
        (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        run(*current, "down", "-v", "--remove-orphans")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore", "verify", "drill", "upgrade"))
    parser.add_argument("--project", help="Explicit Compose project; restore refuses nonempty stores")
    parser.add_argument("--directory", type=Path, help="New backup directory, or the backup to restore")
    parser.add_argument(
        "--receipts", type=Path, help="Complete retained receipt export preceding current container logs"
    )
    args = parser.parse_args()
    if args.action in {"drill", "upgrade"}:
        drill(args.action == "upgrade")
    elif args.action == "verify":
        if not args.directory:
            parser.error("verify requires --directory")
        verify_backup(args.directory)
        print("Backup checksums and receipt chains verified; a restore drill is still required.")
    else:
        if not args.project or not args.directory:
            parser.error("backup/restore require --project and --directory")
        compose = compose_command(args.project)
        if args.action == "backup":
            backup(compose, args.directory, args.receipts)
        else:
            restore(compose, args.directory)


if __name__ == "__main__":
    main()
