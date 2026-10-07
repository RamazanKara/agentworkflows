# Durable agent workflows

AgentWorkflows uses [Temporal](https://docs.temporal.io/develop/python) to persist workflow
history, schedule activities, retry failures, and wait for signals. The Python SDK sends
every model and tool activity through the governed gateway. Provider credentials stay on
the gateway; each worker uses one team's bound gateway key.

## Try research → draft → approval → publish

From the checkout, with Docker Compose, Bash, Make, and Python 3.12+ installed:

```bash
make compose-up
python -m pip install ./sdk/python
export AGENTWORKFLOWS_API_KEY=local-development-only
run=$(python -m agentworkflows.examples.research start "How should our team evaluate AI agents?")
WORKFLOW_ID=$(echo "$run" | python -c 'import json,sys; print(json.load(sys.stdin)["workflow_id"])')
RUN_ID=$(echo "$run" | python -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
python -m agentworkflows.examples.research status "$WORKFLOW_ID" "$RUN_ID"
```

The included worker retrieves sources with the `research` tool, asks a cloud model to
summarize them, drafts a briefing, and waits for approval. Repeat `status` until it shows
`awaiting_approval`, then read the draft. Open <http://localhost:8233> to inspect Temporal
history, activities, failures, and signals. Approve the exact run you reviewed:

```bash
python -m agentworkflows.examples.research approve "$WORKFLOW_ID" "$RUN_ID" --reviewer team-lead
python -m agentworkflows.examples.research result "$WORKFLOW_ID" "$RUN_ID"
curl -s http://localhost:8080/v1/workflow-runs/$RUN_ID \
  -H "Authorization: Bearer $AGENTWORKFLOWS_API_KEY"
```

Use `reject` in place of `approve` to finish without publishing. Approval expires after
seven days. A duplicate or early signal does not approve another draft. The CLI requires
both IDs so a decision cannot accidentally target a later run with the same workflow ID.

The demo uses local cloud-protocol and tool fixtures. Its drafting request deliberately
fails on OpenAI and falls back to Anthropic; the publication is synthetic. No real account,
external publication, or paid model call is used. To use real cloud models, configure an
[approved route and prices](model-selection.md#cloud-routes-milestone-1), replace the tool
URLs below, and pass that route to `start --model your-approved-model`.

Run `make compose-smoke` for the automated end-to-end proof: it waits for the draft, kills
the worker with SIGKILL, submits an approval signal while the worker is offline, starts a
replacement, and verifies publication plus exactly two completed model-call receipts.
Evidence is under `.out/compose/`. `make compose-down` stops the trial and deletes its data;
use `docker compose -f deploy/compose/compose.yaml stop` to retain history and budgets.

## Write your workflow

Use Temporal's decorators and the SDK's two call methods. Do not perform model, network,
or tool I/O directly in workflow code; replay must remain deterministic.

```python
from temporalio import workflow
from agentworkflows.workflows import Budget, WorkflowGateway

@workflow.defn
class Briefing:
    @workflow.run
    async def run(self, topic: str) -> dict:
        gateway = WorkflowGateway(Budget(token_limit=4000, cost_limit_usd=2.0))
        sources = await gateway.tool("research", {"query": topic})
        return await gateway.model(
            [{"role": "user", "content": f"Summarize with citations: {sources}"}],
            model="demo-openai",
            max_tokens=512,
        )
```

Register the workflow and the provided activity on a native Temporal worker:

```python
import asyncio
import os
from temporalio.client import Client
from temporalio.worker import Worker
from agentworkflows.activities import GatewayActivities

async def serve():
    client = await Client.connect("localhost:7233", namespace="default")
    activities = GatewayActivities("http://localhost:8080", os.environ["AGENTWORKFLOWS_API_KEY"])
    async with Worker(client, task_queue="briefing", workflows=[Briefing],
                      activities=[activities.call], max_concurrent_activities=2,
                      max_concurrent_workflow_tasks=2):
        await asyncio.Event().wait()

asyncio.run(serve())
```

Start it with `client.start_workflow(Briefing.run, "your topic", id="briefing-001",
task_queue="briefing")` from another process. The
[complete research example](https://github.com/RamazanKara/agentworkflows/blob/main/sdk/python/agentworkflows/examples/research.py)
also demonstrates `@workflow.signal`, `@workflow.query`, `wait_condition`, and an execution
timeout. Cancel runs with Temporal's client or UI; cancellation stops scheduling subsequent
steps but cannot undo a tool action already sent.

Defaults are five activity attempts with exponential backoff (1 second initially, capped
at 30 seconds), three minutes per attempt, and fifteen minutes including retries and queue
wait. Pass Temporal's `RetryPolicy` and `timedelta` values to `WorkflowGateway` when the
workload needs different timeouts. HTTP requests time out after 120 seconds. Policy, DLP,
and run-budget refusals are non-retryable; transient gateway failures honor `Retry-After`.

## Approve tools and budgets

Add tools to the existing `SandboxPolicySet`, mounted through `SANDBOX_POLICY_PATH` (Helm:
`sandboxPolicy.policy.policies`). The absence of a tool denies its execution. Clients send
a tool name and JSON arguments; only the administrator can set the destination URL:

```yaml
apiVersion: platform.ai/v1alpha1
kind: SandboxPolicySet
spec:
  policies:
    - sandboxId: research-team
      allowedModels: [approved-cloud-model]
      tools:
        research:
          url: https://search.example.com/research
          costUsd: 0.01
          credentialEnv: SEARCH_TOOL_TOKEN
        publish:
          url: https://cms.example.com/briefings
          costUsd: 0.02
          credentialEnv: CMS_TOOL_TOKEN
```

Tool endpoints accept POST JSON and return JSON. `credentialEnv` names a gateway-side
environment variable forwarded as bearer authentication; Helm can inject its Secret via
`providerCredentials`. Tools receive a stable `Idempotency-Key` derived from team, Temporal
run ID, and activity ID. A tool with side effects **must persist and honor this key**.
URLs are fixed and redirects are refused. Input size, secret detection, output DLP, sandbox
budgets, and receipts use the existing gateway controls. A tool's `dataClassification`
defaults to `internal`; explicitly approve a higher classification only for eligible tools.
`GET /v1/tools` lists the caller's approved names.
For Kubernetes, permit tool destinations through the gateway's existing approved egress
policy or internal egress proxy, just as for cloud model endpoints.

Each run defaults to 10,000 tokens and $5 estimated cost. Limits are initialized atomically
and cannot be raised by a retry. They are scoped to the authenticated team and Temporal run
UUID; `GET /v1/workflow-runs/{run_id}` returns only that team's accounting. Every attempted
provider route reserves estimated input plus maximum output at its higher token price.
Reported usage settles that reservation; missing usage and failed/ambiguous attempts keep
the conservative charge. Fallbacks and activity retries reserve separately. Tool attempts
charge their configured `costUsd`, including failed attempts. A refused reservation prevents
the downstream call. Configure both input and output prices on **every** eligible model.

These are estimates, not billing guarantees: input token estimates can differ from provider
usage; an actual overrun is charged and blocks later calls. The SDK's run limit supplements
the existing team budgets. It does not grant models, tools, or credentials.

## Deployment and recovery

Compose installs Temporal with its own PostgreSQL volume, UI, a worker, and persistent Redis
for gateway budget counters. Ports bind only to loopback: gateway 8080, Temporal 7233, UI
8233. The fixture stack has public development passwords and is for evaluation.

For Kubernetes, `deploy/charts/workflows` depends on the pinned
[official Temporal chart](https://github.com/temporalio/helm-charts). It includes a dedicated
PostgreSQL StatefulSet and the example worker. Local and customer Argo CD applications
install it in `workflows`; the gateway accepts that namespace. Before syncing, create the
`temporal-postgres-auth` Secret (`password`) and `workflow-gateway-key` Secret (`api-key`)
in that namespace through your secret manager. The worker key must bind to the intended
team. Configure the gateway's cloud routes and team tool policy first.

To evaluate the chart directly against an existing gateway:

```bash
kubectl create namespace workflows
kubectl -n workflows create secret generic temporal-postgres-auth --from-literal=password="$TEMPORAL_DB_PASSWORD"
kubectl -n workflows create secret generic workflow-gateway-key --from-literal=api-key="$AGENTWORKFLOWS_API_KEY"
helm dependency update deploy/charts/workflows
helm upgrade --install workflows deploy/charts/workflows -n workflows --wait --timeout 10m
```

Build `sdk/python/Dockerfile` with the repository root as context for your worker image;
set `worker.image` to an image reachable by the cluster. Replace the example workflow with
your registered workflows. The bundled Postgres and Redis are single-instance reference
stores: provision backups and tested restores, storage classes, and availability before
team production use. Redis needs AOF persistence and no eviction; run counters have no
window/TTL. Retain them for the run's entire lifetime, including resets; remove the team's
`agentworkflows:sandbox-budget:workflow:<team>:<run-id>` key only after replay/reset is no
longer permitted. Back up both PostgreSQL history and Redis accounting.

Temporal replays recorded activity results after a worker crash without calling the model
again. Activities whose completion was **not recorded** can run again, including the window
between a provider answering and Temporal acknowledging completion. There is no claim of
exactly-once cloud inference. Unknown attempts remain charged; idempotent tools prevent
duplicate publications. Version workflow code using Temporal's deployment/patching rules
before changing the command sequence of running workflows.

Temporal history contains workflow inputs and activity results. Gateway DLP protects the
provider/tool boundary and receipts, not already-written Temporal inputs. Restrict history
access and retention; use Temporal payload encryption for sensitive history. Keep API keys
in worker/gateway environments. Production Temporal and its UI require authentication,
authorization, and TLS; the demo's reviewer string is an attribution label, not verified
identity. Use a separate namespace and worker queue per trust boundary, and restrict who
can submit workflows or approval signals.

| Symptom | Next action |
| --- | --- |
| Run stays queued | Check worker logs, task queue, namespace, and `TEMPORAL_ADDRESS` |
| `workflow_store_required` | Set `SANDBOX_BUDGET_BACKEND=redis`, configure its URL, and enable persistence |
| `workflow_price_missing` | Add input/output prices to that model and every fallback |
| `workflow_*_budget_exceeded` | Inspect the run's usage; start a new reviewed run with an appropriate budget |
| `tool_not_allowed` | Inspect `GET /v1/tools` and the bound team's sandbox policy |
| Approval cannot be submitted | Wait for `awaiting_approval` and use the exact workflow ID and run ID |
