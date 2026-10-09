#!/usr/bin/env python3
"""Start a prepared Compose/kind trial and prove a first approved run within five minutes."""

import argparse
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CLUSTER = "agentworkflows-quickstart"
GATEWAY_IMAGE = "agentworkflows-gateway:quickstart"
WORKER_IMAGE = "agentworkflows-worker:quickstart"


def command(args: list[str], deadline: float, *, capture: bool = False, data: str | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=ROOT,
        input=data,
        text=True,
        capture_output=capture,
        check=True,
        timeout=max(1, deadline - time.monotonic()),
    )
    return result.stdout.strip() if capture else ""


def api(url: str, key: str, path: str, *, body: dict | None = None) -> dict | list:
    request = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def first_run(url: str, key: str, deadline: float) -> str:
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("Gateway did not become ready within the five-minute budget.")
        try:
            api(url, key, "/v1/team")
            break
        except urllib.error.URLError as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                raise
            time.sleep(1)
    models = api(url, key, "/v1/models")["data"]
    if not models or any(not model.get("simulated") for model in models):
        raise RuntimeError("This scripted approval only runs against the credential-free simulated trial.")
    templates = api(url, key, "/v1/workflow-templates")
    template = next(row for row in templates if row["id"] == "research" and row["installable"])
    api(url, key, "/v1/workflow-templates/research/install", body={"version": template["version"]})
    run = api(
        url,
        key,
        "/v1/workflow-runs",
        body={
            "workflow": "ResearchWorkflow",
            "input": {"topic": "How should our team evaluate AI agents?", "model": models[0]["id"]},
            "request_id": str(uuid4()),
        },
    )
    path = f"/v1/workflow-runs/{run['run_id']}"
    approved = False
    while time.monotonic() < deadline:
        detail = api(url, key, path)
        if detail.get("progress", {}).get("stage") == "awaiting_approval" and not approved:
            print(detail["progress"]["draft"])
            api(url, key, path + "/approve", body={"approved": True})
            approved = True
        if detail["status"] == "completed":
            if not approved or detail.get("result", {}).get("status") != "published":
                raise RuntimeError("The run completed without the expected approved publication.")
            if not any(step["action"] == "approval" for step in detail.get("timeline", [])):
                raise RuntimeError("The approved run has no approval audit receipt.")
            verification = api(url, key, "/v1/team/audit/verify")
            if verification.get("ok") is not True or verification.get("checked", 0) < 1:
                raise RuntimeError("The retained audit chain did not verify.")
            return run["run_id"]
        if detail["status"] in {"failed", "canceled", "terminated", "timed_out"}:
            raise RuntimeError(f"Run {run['run_id']} ended as {detail['status']}; inspect the worker logs.")
        time.sleep(1)
    raise TimeoutError(f"Run {run['run_id']} exceeded five minutes; inspect its approval and worker state.")


def kind_trial(deadline: float) -> tuple[str, str, subprocess.Popen]:
    if CLUSTER not in command(["kind", "get", "clusters"], deadline, capture=True).splitlines():
        command(["kind", "create", "cluster", "--name", CLUSTER, "--wait", "60s"], deadline)
    command(["kind", "load", "docker-image", "--name", CLUSTER, GATEWAY_IMAGE, WORKER_IMAGE], deadline)
    kubectl = ["kubectl", "--context", "kind-" + CLUSTER, "-n", CLUSTER]
    namespace = command(
        [*kubectl, "create", "namespace", CLUSTER, "--dry-run=client", "-o", "json"], deadline, capture=True
    )
    command([*kubectl, "apply", "-f", "-"], deadline, data=namespace)
    credential = command(
        [
            *kubectl,
            "create",
            "secret",
            "generic",
            "quickstart-model-key",
            "--from-literal=api-key=fixture-only",
            "--dry-run=client",
            "-o",
            "json",
        ],
        deadline,
        capture=True,
    )
    command([*kubectl, "apply", "-f", "-"], deadline, data=credential)
    config = command(
        [
            *kubectl,
            "create",
            "configmap",
            "cloud-fake",
            "--from-file=cloud-fake.py=deploy/compose/cloud-fake.py",
            "--dry-run=client",
            "-o",
            "json",
        ],
        deadline,
        capture=True,
    )
    command([*kubectl, "apply", "-f", "-"], deadline, data=config)
    labels = {"app": "cloud-fake"}
    fake = {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "cloud-fake"},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": labels},
                    "template": {
                        "metadata": {"labels": labels},
                        "spec": {
                            "containers": [
                                {
                                    "name": "fake",
                                    "image": GATEWAY_IMAGE,
                                    "imagePullPolicy": "IfNotPresent",
                                    "command": ["python", "/fixtures/cloud-fake.py"],
                                    "ports": [{"containerPort": 8000}],
                                    "resources": {
                                        "requests": {"cpu": "50m", "memory": "64Mi"},
                                        "limits": {"cpu": "500m", "memory": "256Mi"},
                                    },
                                    "securityContext": {
                                        "allowPrivilegeEscalation": False,
                                        "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]},
                                    },
                                    "volumeMounts": [{"name": "fixture", "mountPath": "/fixtures", "readOnly": True}],
                                }
                            ],
                            "volumes": [{"name": "fixture", "configMap": {"name": "cloud-fake"}}],
                        },
                    },
                },
            },
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "cloud-fake"},
                "spec": {"selector": labels, "ports": [{"port": 8000, "targetPort": 8000}]},
            },
        ],
    }
    command([*kubectl, "apply", "-f", "-"], deadline, data=json.dumps(fake))
    command(
        [
            "helm",
            "upgrade",
            "--install",
            "quickstart",
            "deploy/charts/agentworkflows",
            "--kube-context",
            "kind-" + CLUSTER,
            "--namespace",
            CLUSTER,
            "-f",
            "deploy/charts/agentworkflows/values-quickstart.yaml",
            "--wait",
            "--timeout",
            "180s",
        ],
        deadline,
    )
    key = base64.b64decode(
        command(
            [*kubectl, "get", "secret", "agentworkflows-admin", "-o", "jsonpath={.data.api-key}"],
            deadline,
            capture=True,
        )
    ).decode()
    forward = subprocess.Popen(
        [*kubectl, "port-forward", "service/inference-gateway", "18080:8080"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return "http://127.0.0.1:18080", key, forward


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target", choices=("compose", "kind"), help="Use the prepared source images; see docs/quickstart.md."
    )
    args = parser.parse_args()
    started = time.monotonic()
    deadline = started + 300
    forward = None
    try:
        if args.target == "compose":
            command(
                [
                    "docker",
                    "compose",
                    "-f",
                    "deploy/compose/compose.yaml",
                    "up",
                    "-d",
                    "--wait",
                    "--wait-timeout",
                    "180",
                    "workflow-worker",
                    "temporal-ui",
                ],
                deadline,
            )
            url = "http://127.0.0.1:" + os.getenv("AGENTWORKFLOWS_GATEWAY_PORT", "8080")
            key = "local-development-only"
        else:
            url, key, forward = kind_trial(deadline)
        run_id = first_run(url, key, deadline)
        elapsed = time.monotonic() - started
        if elapsed >= 300:
            raise TimeoutError("The run succeeded but exceeded the five-minute target.")
        print(f"Approved run {run_id}; audit verified; elapsed {elapsed:.1f}s (target <300s).")
        if forward:
            print(
                f"Console: kubectl --context kind-{CLUSTER} -n {CLUSTER} port-forward service/inference-gateway 18080:8080"
            )
        else:
            print(f"Console: {url}/console/#run/{run_id}")
        return 0
    except (OSError, ValueError, RuntimeError, StopIteration, subprocess.SubprocessError) as exc:
        print(f"Quickstart failed: {exc}. See docs/quickstart.md; no containers or volumes were removed.")
        return 1
    finally:
        if forward:
            forward.terminate()
            forward.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
