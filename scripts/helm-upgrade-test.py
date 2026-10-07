#!/usr/bin/env python3
"""Upgrade the 0.2.0 charts and images in a disposable, CPU-limited kind cluster."""

import contextlib
import importlib.util
import json
import os
import shutil
import tarfile
from pathlib import Path
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("recovery", ROOT / "scripts/workflow-recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
run = recovery.run


def main():
    name = "aw-helm-" + uuid4().hex[:8]
    out = ROOT / ".out" / name
    out.mkdir(parents=True)
    previous = out / "previous"
    previous.mkdir()
    archive = out / "previous.tar"
    run("git", "archive", "--format=tar", "--output", str(archive), recovery.PREVIOUS_RELEASE)
    with tarfile.open(archive) as tar:
        tar.extractall(previous, filter="data")
    helm = shutil.which("helm")
    kubectl = shutil.which("kubectl")
    if not helm or not kubectl:
        raise RuntimeError("Install Helm and kubectl on PATH before running the Helm upgrade drill")
    helm_base = [
        helm,
        "--repository-config",
        str(out / "repositories.yaml"),
        "--repository-cache",
        str(out / "repository-cache"),
    ]
    run(*helm_base, "repo", "add", "temporal", "https://go.temporal.io/helm-charts/")
    if os.name == "nt":
        local_kind = ROOT / ".tools/bin/kind"
        kind = ["wsl.exe", "-d", "Ubuntu", "-e", recovery.docker_path(local_kind) if local_kind.exists() else "kind"]
        docker = ["wsl.exe", "-d", "Ubuntu", "-e", "docker"]
    else:
        kind, docker = ["kind"], ["docker"]
    config = out / "kind.yaml"
    port = int(recovery.free_port())
    os.environ["AGENTWORKFLOWS_GATEWAY_PORT"] = str(port)
    config.write_text(
        yaml.safe_dump(
            {
                "kind": "Cluster",
                "apiVersion": "kind.x-k8s.io/v1alpha4",
                "nodes": [
                    {
                        "role": "control-plane",
                        "extraPortMappings": [{"containerPort": 30080, "hostPort": port, "listenAddress": "127.0.0.1"}],
                    }
                ],
            }
        )
    )
    kubeconfig = out / "kubeconfig"
    kube = [kubectl, "--kubeconfig", str(kubeconfig), "--namespace", "workflows"]
    helm_cmd = [*helm_base, "--kubeconfig", str(kubeconfig), "--namespace", "workflows"]
    override = out / "images.json"
    images = []
    for version, root in (("previous", previous), ("current", ROOT)):
        services = {
            service: {"image": f"agentworkflows/{name}-{role}:{version}"}
            for service, role in (("inference-gateway", "gateway"), ("workflow-worker", "worker"))
        }
        override.write_text(json.dumps({"services": services}))
        compose = recovery.compose_command(name, root, override)
        for service in services:
            print(f"[helm-upgrade] building {service} ({version})", flush=True)
            run(*compose, "build", service)
            images.append(services[service]["image"])
        run(*helm_base, "dependency", "build", str(root / "deploy/charts/workflows"))
    try:
        print("[helm-upgrade] creating isolated kind cluster (2 CPUs, 4 GiB)", flush=True)
        run(
            *kind,
            "create",
            "cluster",
            "--name",
            name,
            "--image",
            "kindest/node:v1.35.1",
            "--config",
            recovery.docker_path(config),
            "--kubeconfig",
            recovery.docker_path(kubeconfig),
            "--wait",
            "120s",
        )
        run(*docker, "update", "--cpus", "2", "--memory", "4g", "--memory-swap", "4g", f"{name}-control-plane")
        run(*kind, "load", "docker-image", *images, "--name", name)
        run(*kube, "create", "namespace", "workflows")
        run(*kube, "create", "secret", "generic", "temporal-postgres-auth", "--from-literal=password=drill-only")
        run(*kube, "create", "secret", "generic", "workflow-gateway-key", "--from-literal=api-key=demo-worker")
        run(*kube, "create", "secret", "generic", "fixture-provider", "--from-literal=key=compose-fake-only")
        run(
            *kube,
            "create",
            "secret",
            "generic",
            "fixture-keys",
            f"--from-file=records.yaml={previous / 'deploy/compose/key-records.yaml'}",
        )
        run(
            *kube,
            "create",
            "configmap",
            "cloud-fake",
            f"--from-file=cloud-fake.py={ROOT / 'deploy/compose/cloud-fake.py'}",
        )
        fake = [
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "cloud-fake"},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": {"app": "cloud-fake"}},
                    "template": {
                        "metadata": {"labels": {"app": "cloud-fake"}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "fake",
                                    "image": images[0],
                                    "command": ["python", "/fixtures/cloud-fake.py"],
                                    "resources": {"limits": {"cpu": "250m", "memory": "128Mi"}},
                                    "volumeMounts": [{"name": "fixtures", "mountPath": "/fixtures"}],
                                }
                            ],
                            "volumes": [{"name": "fixtures", "configMap": {"name": "cloud-fake"}}],
                        },
                    },
                },
            },
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "cloud-fake"},
                "spec": {"selector": {"app": "cloud-fake"}, "ports": [{"port": 8000, "targetPort": 8000}]},
            },
        ]
        run(*kube, "apply", "-f", "-", data=yaml.safe_dump_all(fake).encode())
        routing = yaml.safe_load((previous / "deploy/compose/model-routing.yaml").read_text())["spec"]["models"]
        policies = yaml.safe_load((previous / "deploy/compose/sandbox-policy.yaml").read_text())["spec"]["policies"]
        gateway_values = {
            "fullnameOverride": "inference-gateway",
            "namespace": {"create": False},
            "keda": {"enabled": False},
            "serviceMonitor": {"enabled": False},
            "networkPolicy": {"enabled": False},
            "runtime": {
                "backend": "vllm",
                "modelId": "demo-openai",
                "allowedModels": [r["id"] for r in routing],
                "vllmBaseUrl": "http://cloud-fake:8000/local-overload",
            },
            "routing": {"policy": {"enabled": True, "models": routing}},
            "sandboxPolicy": {"policy": {"enabled": True, "policies": policies}},
            "auth": {
                "enabled": True,
                "keyRecords": {"existingSecret": {"name": "fixture-keys", "key": "records.yaml"}},
            },
            "providerCredentials": [
                {"env": env, "secretName": "fixture-provider", "secretKey": "key"}
                for env in ("COMPOSE_PROVIDER_KEY", "OPENAI_API_KEY")
            ],
            "budget": {"backend": "redis", "redisUrl": "redis://budget-redis:6379/0", "estimatedTokenLimit": 200000},
            "traceability": {"defaultSandboxId": "demo"},
            "adminConsole": {"enabled": True},
            "receipts": {"enabled": True},
            "service": {"type": "NodePort", "nodePort": 30080},
        }
        # 0.2.0 required this operator override to boot its SQL stores with chart 1.7.0.
        worker_values = {
            "worker": {"gatewayUrl": "http://inference-gateway:8080"},
            "temporal": {
                "server": {
                    "config": {
                        "persistence": {
                            "datastores": {
                                name: {"sql": {"connectProtocol": "tcp"}} for name in ("default", "visibility")
                            }
                        }
                    }
                }
            },
        }
        gateway_file, worker_file = out / "gateway.yaml", out / "workflows.yaml"
        run(
            *helm_cmd,
            "install",
            "budget",
            str(previous / "deploy/charts/budget-redis"),
            "--set",
            "namespace.create=false,networkPolicy.enabled=false",
            "--wait",
            "--timeout",
            "5m",
        )

        def deploy(version, root, upgrading):
            gateway_values["image"] = {"repository": f"agentworkflows/{name}-gateway", "tag": version}
            worker_values["worker"]["image"] = f"agentworkflows/{name}-worker:{version}"
            if upgrading:
                gateway_values.update(replicaCount=2, workflows={"temporalAddress": "temporal-frontend:7233"})
                gateway_values["traceability"]["auditChainStore"] = {
                    "backend": "redis",
                    "redisUrl": "redis://budget-redis:6379/0",
                }
                worker_values["worker"].update(replicaCount=2, podDisruptionBudget={"enabled": True, "minAvailable": 1})
                worker_values["temporal"] = {"schema": {"useHelmHooks": True}}
            gateway_file.write_text(yaml.safe_dump(gateway_values))
            worker_file.write_text(yaml.safe_dump(worker_values))
            action = "upgrade" if upgrading else "install"
            # The new gateway readiness requires Temporal; upgrade its servers/worker first.
            charts = (
                (("workflows", worker_file), ("inference-gateway", gateway_file))
                if upgrading
                else (("inference-gateway", gateway_file), ("workflows", worker_file))
            )
            for release, values in charts:
                print(f"[helm-upgrade] {action} {release}", flush=True)
                run(
                    *helm_cmd,
                    action,
                    release,
                    str(root / f"deploy/charts/{release}"),
                    "-f",
                    str(values),
                    "--wait",
                    "--wait-for-jobs",
                    "--timeout",
                    "10m",
                )

        def pvc_ids():
            return {
                p["metadata"]["name"]: p["metadata"]["uid"]
                for p in json.loads(run(*kube, "get", "pvc", "-o", "json"))["items"]
            }

        def schema_versions():
            return {
                db: run(
                    *kube,
                    "exec",
                    "statefulset/temporal-postgres",
                    "--",
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
                for db in recovery.DATABASES
            }

        def gateway_state():
            code = """
import hashlib, json, os, redis
r = redis.Redis.from_url(os.environ['SANDBOX_BUDGET_REDIS_URL'])
print(json.dumps({k.decode(): hashlib.sha256(r.dump(k)).hexdigest() for k in sorted(r.scan_iter())
                  if b'audit-chain' not in k and not k.endswith(b':schema-version')}))
"""
            return json.loads(run(*kube, "exec", "deployment/inference-gateway", "--", "python", "-c", code))

        deploy("previous", previous, False)
        started = recovery.request("/v1/workflow-runs", {"input": {"topic": "Helm upgrade recovery"}})
        run_id = started["run_id"]
        before = recovery.wait_run(run_id, "awaiting_approval")
        usage, state, pvcs = recovery.request("/v1/usage"), gateway_state(), pvc_ids()
        versions = schema_versions()
        assert all(versions.values())
        log = run(*kube, "logs", "deployment/inference-gateway")
        (out / "receipts.jsonl").write_bytes(log)
        run(
            recovery.sys.executable,
            str(ROOT / "scripts/audit-anchor.py"),
            str(out / "receipts.jsonl"),
            "--output",
            str(out / "anchor.json"),
        )
        deploy("current", ROOT, True)
        assert pvc_ids() == pvcs and gateway_state() == state, "Helm upgrade replaced storage or lost gateway state"
        assert schema_versions() == versions, "Helm upgrade lost the SQL schema history"
        after = recovery.wait_run(run_id, "awaiting_approval")
        assert after["timeline"] == before["timeline"] and after["budget"] == before["budget"]
        assert recovery.request("/v1/usage") == usage
        recovery.request(f"/v1/workflow-runs/{run_id}/approve", {"approved": True}, key="demo-approver")
        finished = recovery.wait_run(run_id, "completed")
        assert finished["result"]["status"] == "published"
        assert len([row for row in finished["timeline"] if row["action"] == "model_call"]) == 2
        (out / "receipts.jsonl").write_bytes(
            log
            + b"\n"
            + run(*kube, "logs", "-l", "app.kubernetes.io/name=inference-gateway", "--tail=-1", "--max-log-requests=2")
        )
        recovery.verify_receipts(out)
        report = {
            "status": "pass",
            "previous_release": recovery.PREVIOUS_RELEASE,
            "run_id": run_id,
            "retained_pvcs": pvcs,
            "schema_versions": versions,
            "gateway_keys_preserved": len(state),
            "receipt_chain_verified": True,
            "gateway_replicas": 2,
            "worker_replicas": 2,
        }
        (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    except Exception:
        for deployment in (
            "workflow-worker",
            "inference-gateway",
            "temporal-frontend",
            "temporal-history",
            "temporal-matching",
        ):
            with contextlib.suppress(RuntimeError):
                (out / f"{deployment}.log").write_bytes(run(*kube, "logs", f"deployment/{deployment}", "--tail=100"))
        raise
    finally:
        run(*kind, "delete", "cluster", "--name", name)


if __name__ == "__main__":
    main()
