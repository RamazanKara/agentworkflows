# Agent-sandbox integration

Agent workspaces run as `agents.x-k8s.io/v1beta1` `Sandbox` resources managed by the
vendored `kubernetes-sigs/agent-sandbox` controller. It is the standard workspace runtime in
release `v0.9.0`.

The decisions are recorded in [ADR 0009](adr/0009-adopt-agent-sandbox-workspace-runtime.md)
and [ADR 0010](adr/0010-agent-sandbox-standard-runtime.md). This page describes the current
contract rather than the implementation history.

## Installation

The controller manifests are vendored under `deploy/vendor/agent-sandbox/v0.5.0/` with
recorded SHA-256 checksums. Both Argo CD profiles contain an
`agent-sandbox-controller` application. The direct quickstart path installs the same files with:

```bash
make agent-sandbox-install
```

The install uses server-side apply because the CRDs are too large for the client-side
last-applied annotation.

## Workspace resource

`deploy/charts/agent-workspace` always renders one `Sandbox`. Its name is
`sandbox.id`, and the controller gives the managed pod the same name. The chart also creates:

- the workspace namespace, service account, RBAC, quota, and limit range;
- a writable PVC mounted at `/workspace`;
- a configuration map with the gateway and RAG service addresses;
- default-deny ingress and egress policies plus explicit DNS, gateway, RAG, and catalog-approved
  CIDR rules.

The pod template runs as UID/GID `10001`, uses `RuntimeDefault` seccomp, drops all Linux
capabilities, disables privilege escalation, and makes the root filesystem read-only. The
default Kubernetes service-account token mount is disabled.

## Kernel isolation

The controller-managed pod is a lifecycle and policy boundary that shares the node kernel by
default; `sandbox.runtimeClassName` is empty in the checked-in local and customer values. For a
separate kernel boundary, set it to a cluster-provided runtime such as gVisor or Kata, and verify
that the runtime exists on every node that may host a workspace.

## Platform credential

The chart projects a service-account token at `/var/run/platform/token`. It is scoped to the
`inference-gateway` audience, expires after 600 seconds by default, and is rotated by the kubelet.
The gateway only accepts it when JWT/JWKS verification is configured against the cluster issuer.

Handle this token as a credential. Audience binding and expiry scope it to the gateway for a short
window.

## Network boundary

The chart's NetworkPolicies form the network boundary for the direct `Sandbox` path. They require
a CNI that enforces NetworkPolicy; the local profile uses Calico. The smoke check requires an
enforcing CNI for egress evidence.

Every external CIDR entry needs a `catalogRef` from
`platform/network/egress-catalog.yaml`. Approved destinations receive workspace traffic, so keep
the catalog narrow and review it as an exfiltration boundary.

## Updates and lifecycle

`workspace.shutdownTime` and `workspace.shutdownPolicy` can bound a workspace lifetime. They are
unset by default.

The v0.5.0 controller keeps its singleton pod running when the `Sandbox` pod template changes.
After a chart update, delete the managed pod so the controller recreates it from the current
template. `make agent-sandbox-smoke` detects image or volume drift and performs that refresh.

## Validation

After the controller and workspace application are synced, run:

```bash
make agent-sandbox-smoke
```

The check verifies controller readiness, the `Sandbox` and pod state, non-root execution, the
read-only root filesystem, writable workspace storage, absence of the ambient token, the projected
token audience, working DNS, and blocked non-catalog egress.

`make agent-sandbox-demo` adds the governed model-call and audit-receipt walkthrough used by the
README animation.

## Container workflow steps

`await WorkflowGateway().container("coder", {"task": "Review this code"})` executes an
administrator-approved command in an **existing** agent-sandbox workspace. It reuses this
chart and controller, so the worker runs it without an additional execution service or Docker socket.
The included `CodeWorkflow` and
[container agent](https://github.com/RamazanKara/agentworkflows/blob/main/sdk/python/agentworkflows/examples/container_agent.py)
write `review.txt` inside `/workspace` and call the gateway with a short-lived credential.

Use a dedicated workspace namespace with an enforcing NetworkPolicy CNI. The normal workspace
chart defaults also allow RAG and a projected platform token; disable both for workflow code
so it has only the step's gateway credential. With the controller already installed, build
and push your worker image to your own registry, then use that same image for the example
workspace. The image from `sdk/python/Dockerfile` includes Python, the runner, and kubectl.

```bash
helm upgrade --install code-workspace deploy/charts/agent-workspace \
  --set namespace.name=team-code --set sandbox.id=coder \
  --set workspace.container.image="$WORKER_IMAGE" \
  --set workspace.credentials.projectedToken.enabled=false \
  --set networkPolicy.rag.enabled=false \
  --set sandbox.externalEgressAllowed=false
```

Keep `networkPolicy.enabled=true`, `allowedEgressCidrs=[]`, and the chart's hardening defaults.
Set `networkPolicy.gateway.namespace` and `port` if your gateway uses different values.
After changing an existing Sandbox template, recreate its pod as described above.
The worker checks the actual pod's owner, hardening, volumes, image, and namespace policies
before execution. Extra egress policies, external CIDRs, sidecars, secret/projected volumes,
or ambient environment credentials produce an actionable refusal. An enforcing CNI is a
deployment prerequisite; confirm enforcement with `make agent-sandbox-smoke`.

Add the agent to the team's gateway policy (substitute your approved routes/origins):

```yaml
workflows:
  CodeWorkflow:
    allowedProviders: [openai]
    allowedModels: [demo-openai]
    allowedTools: []
    allowedEgress: [https://api.openai.com]
    tokenLimit: 2000
    costLimitUsd: 1
    agents:
      coder:
        namespace: team-code
        sandbox: coder
        command: [python, /app/agentworkflows/examples/container_agent.py]
        timeoutSeconds: 120
        costUsd: 0.01
```

The route named `demo-openai` must exist in this gateway; the Compose fixture route uses
`http://cloud-fake:8000`, while a live OpenAI route uses its configured origin. The example
agent's model is explicit in its source. Credentials for providers stay on the gateway.
Commands, namespace, and Sandbox names come only from this reviewed policy. Agent input is JSON on stdin, scanned for secrets before authorization.

In your existing workflows Helm values, grant the worker access to that workspace:

```yaml
worker:
  image: your-registry/agentworkflows-worker:your-tag
  gatewayUrl: http://inference-gateway.inference.svc.cluster.local:8080
  workspaces:
    - namespace: team-code
      sandbox: coder
```

Upgrade the workflows release with those values. Its worker service account receives read
access to the named Sandbox/pod and namespace policies, and exec access to that pod only.
Pod creation, network-policy changes and Secret reads stay outside its role. Use the actual gateway
Service DNS URL: container steps validate that the approved egress rule reaches its namespace
and port. Workers running outside Kubernetes instead need kubectl and equivalently scoped
kubeconfig access. The example worker already registers `CodeWorkflow` on `research`:

```bash
python - <<'PY'
import asyncio, os, uuid
from datetime import timedelta
from temporalio.client import Client
from agentworkflows.examples.frameworks import CodeWorkflow

async def run():
    client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
    print(await client.execute_workflow(
        CodeWorkflow.run, "Give three checks for a code review",
        id=f"code-{uuid.uuid4()}", task_queue="research", execution_timeout=timedelta(minutes=5),
    ))
asyncio.run(run())
PY
```

The gateway issues an opaque credential scoped to the team, run, step, and classification;
it expires with `timeoutSeconds` (at most ten minutes). Only governed model/tool endpoints
accept it, even when the agent omits correlation headers. The worker passes it through stdin,
keeping it out of command-line arguments and Temporal results, and revokes it when the step
finishes. Team keys, provider keys and Kubernetes credentials stay outside the workspace. Failed/crashed
steps retain their conservative cost; expiry bounds grants after a lost worker. A Redis lease
prevents simultaneous workflow steps in one workspace. Failed execution holds the lease until
its timeout plus 30 seconds, since remote work may still be stopping.

Container steps default to **one attempt**, so code side effects run once. The runner bounds runtime and output, kills its process group, and sends output
through gateway DLP before returning it to Temporal. `agent_start` and `agent_exec` receipts
correlate authorization and completion; a start receipt without a completion receipt marks
an unknown outcome. Each model/tool call has its own receipt. Workspace data persists on its PVC; dedicate workspaces to a
trust boundary and clean/recreate them between untrusted workloads.

Compose validates framework/MCP flows without Kubernetes. Container unit tests validate the
runner contract and refusals; `make agent-sandbox-smoke` verifies the actual cluster boundary.
Use a gVisor/Kata RuntimeClass when a separate kernel boundary is required.

## Operator hardening

- Set an isolation `RuntimeClass` to give workspaces a kernel separate from the node.
- Add CNI or mesh mTLS to encrypt workspace connections; NetworkPolicy controls which connections
  are allowed.
- Review each approved external service as part of the workspace's effective permissions.
- The gateway audit chain records governed model and tool calls; use node or runtime monitoring for
  process and file activity inside the workspace.
- Configure PVC availability, backup, retention, and secure deletion in the cluster storage system.
