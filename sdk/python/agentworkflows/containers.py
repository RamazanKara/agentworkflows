"""Execute approved commands in the existing hardened agent-sandbox workspace."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from temporalio.exceptions import ApplicationError

from agentworkflows import _raise_for_status
from agentworkflows.workflows import Call


async def _kubectl(*args: str, data: bytes | None = None, timeout: float = 20) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            "kubectl",
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise ApplicationError(
            "Install kubectl on the worker and bind its identity to the approved workspace.", non_retryable=True
        ) from None
    try:
        assert process.stdin is not None and process.stdout is not None
        async with asyncio.timeout(timeout):
            process.stdin.write(data or b"")
            await process.stdin.drain()
            process.stdin.close()
            output = bytearray()
            while chunk := await process.stdout.read(65536):
                output.extend(chunk)
                if len(output) > 1_048_576:
                    raise ApplicationError(
                        "Workspace output exceeds 1 MiB; return a smaller result.", non_retryable=True
                    )
            if await process.wait():
                raise ApplicationError(
                    "Workspace command failed; check Sandbox readiness, worker RBAC, and agent image.",
                    non_retryable=True,
                )
            return bytes(output)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def validate_workspace(sandbox: dict[str, Any], pod: dict[str, Any], policies: dict[str, Any], gateway: str) -> None:
    message = (
        "Workspace is not isolated for workflow execution. Use the agent-workspace chart with gateway-only "
        "egress, no projected token, and one hardened workspace container; see docs/agent-sandbox-integration.md."
    )
    spec = pod["spec"]
    security = spec.get("securityContext", {})
    containers = spec.get("containers", [])
    url = urlsplit(gateway)
    host = (url.hostname or "").split(".")
    if "svc" not in host or host.index("svc") < 2:
        raise ApplicationError(
            "Container workers must use the gateway's Kubernetes service DNS URL.", non_retryable=True
        )
    namespace = host[host.index("svc") - 1]
    gateway_rule = {
        "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": namespace}}}],
        "ports": [{"protocol": "TCP", "port": url.port or (443 if url.scheme == "https" else 80)}],
    }
    dns_rule = {
        "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}],
        "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
    }
    network = {item["metadata"]["name"]: item["spec"] for item in policies["items"]}
    deny = network.get("agent-workspace-default-deny", {})
    approved = network.get("agent-workspace-approved-egress", {})
    valid = (
        sandbox.get("apiVersion") == "agents.x-k8s.io/v1beta1"
        and any(
            owner.get("uid") == sandbox["metadata"]["uid"] and owner.get("kind") == "Sandbox"
            for owner in pod["metadata"].get("ownerReferences", [])
        )
        and pod.get("status", {}).get("phase") == "Running"
        and spec.get("automountServiceAccountToken") is False
        and not any(
            spec.get(key) for key in ("hostNetwork", "hostPID", "hostIPC", "initContainers", "ephemeralContainers")
        )
        and security.get("runAsNonRoot") is True
        and security.get("runAsUser") == 10001
        and security.get("runAsGroup") == 10001
        and security.get("seccompProfile") == {"type": "RuntimeDefault"}
        and len(containers) == 1
        and containers[0].get("name") == "workspace"
        and all(set(volume) <= {"name", "emptyDir", "persistentVolumeClaim"} for volume in spec.get("volumes", []))
        and set(network) == {"agent-workspace-default-deny", "agent-workspace-approved-egress"}
        and deny.get("podSelector") == {}
        and set(deny.get("policyTypes", [])) == {"Ingress", "Egress"}
        and not deny.get("ingress")
        and not deny.get("egress")
        and approved.get("podSelector") == {}
        and approved.get("policyTypes") == ["Egress"]
        and not approved.get("ingress")
        and gateway_rule in approved.get("egress", [])
        and all(rule in (gateway_rule, dns_rule) for rule in approved.get("egress", []))
    )
    if valid:
        container = containers[0]
        context = container.get("securityContext", {})
        valid = (
            context.get("allowPrivilegeEscalation") is False
            and context.get("readOnlyRootFilesystem") is True
            and not context.get("privileged")
            and context.get("capabilities") == {"drop": ["ALL"]}
            and context.get("runAsUser", 10001) == 10001
            and context.get("runAsGroup", 10001) == 10001
            and context.get("runAsNonRoot", True) is True
            and context.get("seccompProfile", {"type": "RuntimeDefault"}) == {"type": "RuntimeDefault"}
            and context.get("procMount", "Default") == "Default"
            and not container.get("env")
            and not container.get("envFrom")
            and container["image"] == sandbox["spec"]["podTemplate"]["spec"]["containers"][0]["image"]
        )
    if not valid:
        raise ApplicationError(message, non_retryable=True)


async def run_container(client: httpx.AsyncClient, call: Call, headers: dict[str, str]) -> dict[str, Any]:
    path = f"/v1/agents/{quote(call.tool, safe='')}"
    response = await client.post(path + "/start", json=call.payload, headers=headers)
    _raise_for_status(response)
    plan = response.json()
    result = {"exit_code": -1, "output": ""}
    try:
        namespace, name = plan["namespace"], plan["sandbox"]
        sandbox = json.loads(await _kubectl("-n", namespace, "get", "sandbox", name, "-o", "json"))
        pod = json.loads(await _kubectl("-n", namespace, "get", "pod", name, "-o", "json"))
        policies = json.loads(await _kubectl("-n", namespace, "get", "networkpolicies", "-o", "json"))
        validate_workspace(sandbox, pod, policies, str(client.base_url))
        payload = {**plan, "gateway_url": str(client.base_url), "headers": headers}
        output = await _kubectl(
            "-n",
            namespace,
            "exec",
            "-i",
            name,
            "-c",
            "workspace",
            "--",
            "python",
            "-m",
            "agentworkflows.container_runner",
            data=json.dumps(payload).encode(),
            timeout=plan["timeout_seconds"] + 10,
        )
        result = json.loads(output)
    finally:
        # Never write the grant or raw container output to Temporal history.
        async def finish() -> httpx.Response:
            return await client.post(
                path + "/finish", headers=headers, json={"credential_id": plan["credential_id"], **result}
            )

        task = asyncio.create_task(finish())
        try:
            response = await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
    _raise_for_status(response)
    return response.json()
