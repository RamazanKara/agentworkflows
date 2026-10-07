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
export AGENTWORKFLOWS_API_KEY=demo-builder
agentworkflows team
run=$(agentworkflows runs start --input '{"topic":"How should our team evaluate AI agents?"}')
RUN_ID=$(echo "$run" | python -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
agentworkflows runs inspect "$RUN_ID"
agentworkflows runs list
```

The included worker retrieves sources with the `research` tool, asks a cloud model to
summarize them, drafts a briefing, and waits for approval. Repeat `inspect` until it shows
`awaiting_approval`, then read the draft. Open <http://localhost:8233> to inspect Temporal
history, activities, failures, and signals. Approve the exact run you reviewed:

```bash
AGENTWORKFLOWS_API_KEY=demo-approver agentworkflows runs approve "$RUN_ID"
agentworkflows runs inspect "$RUN_ID"
curl -s http://localhost:8080/v1/workflow-runs/$RUN_ID \
  -H "Authorization: Bearer $AGENTWORKFLOWS_API_KEY"
```

Use `approve --reject` to finish without publishing. Approval expires after seven days.
The API records the authenticated key name or verified JWT subject; clients cannot supply
a reviewer name. A Temporal update accepts only one decision for the waiting draft.
Early or conflicting decisions return `409 approval_not_waiting`. Retrying the same identity's
decision is idempotent, including after a lost response. Run IDs identify exact executions.

The demo uses local cloud-protocol and tool fixtures. Its drafting request deliberately
fails on OpenAI and falls back to Anthropic; the publication is synthetic. No real account,
external publication, or paid model call is used. To use real cloud models, configure an
[approved route and prices](model-selection.md#cloud-routes-milestone-1), replace the tool
URLs below, and set `model` in the run's JSON input to your approved model ID.

Run `make compose-smoke` for the automated end-to-end proof: it waits for the draft, kills
the worker with SIGKILL while approval is waiting, starts a replacement, approves through
the authenticated API, and verifies publication plus exactly two completed model-call receipts.
It also checks roles, team isolation, timelines, shared spend, cancellation, and retry.
Evidence is under `.out/compose/`. `make compose-down` stops the trial and deletes its data;
use `docker compose -f deploy/compose/compose.yaml stop` to retain history and budgets.

## Web console

After `make compose-up`, open <http://127.0.0.1:8080/console>:

1. Sign in with `local-development-only` (demo admin). Keep the local fake providers on
   **Get started**; no cloud key or paid call is needed.
2. Choose **Run workflow**, keep `ResearchWorkflow` and the suggested topic/model, and
   select **Start run**. Its detail page refreshes while the workflow is active.
3. Open **Approvals**, read the draft, then **Approve** or **Reject**. Approval allows
   the configured publish tool to run; rejection finishes without publishing.
4. Open the run from **Workflow runs**. Expand **Receipt** and **Step logs** in its timeline
   to inspect provider/model, usage, cost, routing attempts, and the full redacted receipt.
5. Open **Costs** for current-window team, provider, and workflow costs. **Providers & budgets**
   shows admins key presence and limits, with a copyable fragment and links for configuring
   real providers through the existing reviewed policy and gateway Secret deployment.

Use project, workflow, and status filters to find runs. **Load more** advances through older
index pages, including pages with no matches. The approvals inbox walks every page of every
available project. Expired Temporal executions are omitted from lists; direct inspection
reports that the run is unavailable.

**Add team / identity** verifies another team key or signed JWT before adding it to the
switcher. Each selection uses that credential's server-verified team, role, and project
access; a browser header cannot grant membership. Credentials live only in memory and are
cleared on reload/sign-out. There is no separate password store or membership editor.
Use `demo-builder`, `demo-approver`, `demo-viewer`, or `demo-other-team` to explore roles.

The gateway image includes the built console; no Node server or extra container runs in
production. Compose enables it. For a standalone gateway Helm release, set
`adminConsole.enabled=true`; the umbrella chart uses `inference-gateway.adminConsole.enabled=true`.
Keep the existing team auth, Redis, workflow worker, TLS ingress, and Temporal settings.
The same gateway host serves `/console/` and `/v1/`; no cross-origin API setting is needed.

Run `make compose-smoke` for browser checks as well as gateway/worker recovery checks.
It requires Node.js 24/npm and installs Chromium on first use. Linux hosts missing browser
libraries can run `cd src/inference-gateway/console && npm ci && npx playwright install --with-deps chromium`.
CI installs those libraries before validation. Keyboard users can use **Skip to content**,
standard Tab/Enter navigation, and native disclosure controls. Tables scroll within their
panels on small screens.

Step logs here are gateway event/routing records, not worker stdout or full Temporal history.
Receipt hashes must still be checked against the retained audit export and head anchors.
Provider keys and budget edits remain reviewed deployment configuration; key presence does
not prove live provider acceptance.

## Teams, projects, and roles

A **team is the existing sandbox ID**. Projects group and restrict run access. Operators
review the existing `API_KEY_RECORDS_PATH` and `SANDBOX_POLICY_PATH` files, then recreate
the gateway. The console and CLI use the same authenticated workflow API.
Managed teams require authentication, `SANDBOX_BUDGET_ENABLED=true`,
`SANDBOX_BUDGET_BACKEND=redis`, and `AUDIT_LOG_ENABLED=true`; Compose sets these already.

| Role | Read runs/reports | Start, cancel, retry, call models | Approve/reject |
| --- | --- | --- | --- |
| admin | Yes | Yes | Yes |
| builder | Yes | Yes | No |
| approver | Yes | No | Yes |
| viewer | Yes | No | No |

The public Compose keys are `local-development-only` (admin), `demo-builder`,
`demo-approver`, `demo-viewer`, and `demo-worker`. Use randomly generated keys outside
the trial. Add a team policy and a key record for each member:

```yaml
# Entry under records in key-records.yaml; store only the random key's SHA-256 digest.
- name: alice
  sha256: REPLACE_WITH_SHA256_OF_RANDOM_KEY
  sandbox: research-team
  role: builder
  project: briefing  # omit to grant this role across the team's projects
```

```yaml
# Entry under spec.policies in the existing SandboxPolicySet.
- sandboxId: research-team
  projects: [briefing, engineering]
  providerCredentials:
    openai: RESEARCH_OPENAI_KEY
    anthropic: RESEARCH_ANTHROPIC_KEY
  budgets:
    estimatedTokenLimit: 200000
    costLimitUsd: 25
  # Copy reviewed tools/workflows from deploy/compose/sandbox-policy.yaml,
  # then replace model IDs and allowedEgress with approved destinations.
```

`providerCredentials` names gateway environment variables/Secret references, never keys.
Each route, including fallback and streaming, selects the bound team's provider key;
a missing provider mapping fails closed for managed teams. `agentworkflows team` shows
available projects and provider names without exposing secrets. Project-bound credentials
cannot read or control another project's runs. Identity comes from a verified sandbox
binding, never a caller-supplied team/project header. Existing signed JWTs can supply `role`
and optional `project` claims with a nonempty `sub`; configure `JWT_TENANT_CLAIM` and control claim issuance.
Legacy unbound keys cannot use the team run API. Enabling projects requires roles on the
team's existing credentials. Team onboarding remains declarative; there is no membership UI.

Use one trusted worker per team with a bound builder key carrying `workflows:execute`.
Set `TEMPORAL_TASK_QUEUE=research-team-workflows`; the API selects that queue from the
verified team. Human credentials do not carry the worker scope. In Helm, set `worker.team`
and `worker.existingSecret`. Workers and Temporal operators are trusted: end users must
not receive direct Temporal access or worker keys.

## Authenticated workflow API

The CLI uses these endpoints with `Authorization: Bearer <team-key>`:

| Method and path | Purpose |
| --- | --- |
| `GET /v1/team` | Discover role, projects, providers, and spend limit |
| `GET /v1/workflow-policies` | Discover approved workflow types and limits |
| `POST /v1/workflow-runs` | Start with `workflow`, JSON `input`, optional `project` and UUID `request_id` |
| `GET /v1/workflow-runs?project=briefing&offset=0&limit=20&status=awaiting_approval` | Page through project runs; optional `status` and `workflow` filters |
| `GET /v1/workflow-runs/{run_id}` | Status, draft, budget, step timeline with receipt IDs |
| `POST /v1/workflow-runs/{run_id}/cancel` | Request cancellation of this exact execution |
| `POST /v1/workflow-runs/{run_id}/retry` | Start a fresh execution after failure/cancellation |
| `POST /v1/workflow-runs/{run_id}/approve` | Submit `{"approved":true}` or `false` |
| `GET /v1/usage` | Current-window team/project usage, provider costs, and `spend.workflows` costs |

```bash
curl -s http://127.0.0.1:8080/v1/workflow-runs \
  -H 'Authorization: Bearer demo-builder' -H 'Content-Type: application/json' \
  -d '{"workflow":"ResearchWorkflow","input":{"topic":"Evaluate team agents"},"project":"default"}'
agentworkflows runs cancel RUN_ID
agentworkflows runs retry FAILED_RUN_ID
agentworkflows usage
```

Retry accepts failed, canceled, terminated, or timed-out runs. It starts a new execution
and run budget; team spend is retained. Repeating retry on a failed run returns the same
replacement. Review side effects first: tools may execute again. Cancellation cannot undo
already-sent actions. `runs list` returns `next_offset`; pass it as `--offset` for the next page.
`runs start` defaults to `ResearchWorkflow`; use `runs start YourWorkflow --input '<JSON>'
--project engineering` for another registered workflow. Input is its single argument.

Supply a stable UUID `request_id` when retrying an ambiguous start. The CLI prints a retry
ID with start errors. Reusing that ID with different input returns a conflict.
`PUT /v1/workflow-runs/{run_id}` remains the worker's immutable budget initialization.
The Python `GatewayClient` exposes `team`, `start_run`, `runs`, `run`, `cancel_run`,
`retry_run`, and `approve_run` with the same semantics.
Custom workflows expose a `status` query returning `stage` for progress and, when they
support approvals, a `review(approved, reviewer)` update returning whether the waiting
decision was accepted. The research example implements both.

## Operate the service

`agentworkflows usage` aggregates providers and tools for the bound team or project.
`spend` shows the UTC-aligned cost window, limit, and reserved-plus-spent USD. Its length
uses `SANDBOX_BUDGET_WINDOW_SECONDS` (default 86400); zero means lifetime accounting.
Workflow cost rows accumulate from this version onward, in the same window as provider
costs. They count governed model/tool calls, not run starts, and exclude standalone calls.
Project-bound credentials see only their project's rows. Earlier windows are not backfilled.
Token/request budgets retain their existing window semantics. Spend reservations are
atomic in the existing Redis, covering eligible fallbacks before a call. Managed-team
calls retry through the caller or Temporal so each attempt has a budget and receipt.
Unused fallback capacity is refunded; measured usage replaces the successful
attempt's reservation. Failed/unknown attempts remain conservatively charged. Configure
prices for every route, including local models; tools charge per attempt. These are
configured-price estimates, not provider invoices or billing guarantees.

Persist Redis with AOF and no eviction. Run metadata, timeline indices, and cost windows
survive gateway/worker restarts; back up Redis alongside Temporal PostgreSQL. Historical
cost keys remain available for operator export under `...:<team>:cost:<window-start>`;
the API reports the current window. Retain run metadata/timeline keys with Temporal history
and audit evidence. Expired Temporal executions return 404; retained receipts remain in
the audit export. Run input is stored in Redis for retry, and inputs/drafts/results are in
Temporal; restrict access and retention for both stores.

The **AgentWorkflows Team Operations** Grafana dashboard sits beside existing dashboards
in `deploy/observability/dashboards`: throughput, failures, run states, approvals waiting,
and shared spend. State/spend gauges refresh every 30 seconds; queries use `max` across
replicas to avoid double counting. Alerts cover stalled throughput, failed runs, approvals
waiting 30 minutes, spend above 80%, and stale collection. The existing Kubernetes
observability deployment loads them; Compose does not install Grafana.

The timeline is a convenience index of gateway receipts; write failures are logged and
may leave gaps. Verify the separately retained audit export and external head anchors.
Hashes establish linkage within retained chains; they do not prove unreported tool activity,
model correctness, or that a release was signed/published.

## Write your workflow

Use Temporal's decorators and the SDK's call methods. Do not perform model, network,
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
    async with Worker(client, task_queue="research-team-workflows", workflows=[Briefing],
                      activities=[activities.call], max_concurrent_activities=2,
                      max_concurrent_workflow_tasks=2):
        await asyncio.Event().wait()

asyncio.run(serve())
```

Register `Briefing` in the team workflow policy, then start with
`agentworkflows runs start Briefing --input '"your topic"' --project briefing`. The
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

## Bring your agent

With the Compose stack running, run all four examples in its existing worker:

```bash
docker compose -f deploy/compose/compose.yaml run --rm --no-deps workflow-worker \
  python -m agentworkflows.examples.frameworks "How should we evaluate team agents?"
```

The result includes the run ID and answers from plain OpenAI and Anthropic clients,
OpenAI Agents SDK, and LangGraph. The first agent calls `team.search` through MCP.
Open Temporal at <http://localhost:8233> and inspect that run; use its ID with
`GET /v1/workflow-runs/{run_id}` for usage. `make compose-smoke` checks these calls,
policy refusals, DLP, and their receipts. No cloud credentials are needed.

For your own worker, install `python -m pip install './sdk/python[frameworks]'`.
Register your async agent function with the existing activity:

```python
import os
from agentworkflows.activities import GatewayActivities

async def briefing(context, arguments):
    sources = await context.tool("team.search", {"query": arguments["topic"]})
    response = await context.openai().chat.completions.create(
        model="demo-openai", max_tokens=128,
        messages=[{"role": "user", "content": f"Summarize: {sources}"}],
    )
    return response.choices[0].message.content

activities = GatewayActivities(
    "http://localhost:8080", os.environ["AGENTWORKFLOWS_API_KEY"], agents={"briefing": briefing},
)
# Register activities.call on your Temporal worker, as above.
# Inside your workflow:
# answer = await WorkflowGateway().agent("briefing", {"topic": topic})
```

`context.openai()` and `context.anthropic()` configure the official async clients with
the gateway URL, team authentication, run/step headers, and no nested HTTP retries.
Use `context.agents_model(model)` with the Agents SDK's `Agent` and `Runner.run`;
set `RunConfig(tracing_disabled=True)` to keep prompts out of external tracing.
In LangGraph, use `context.openai()` in your graph nodes and `graph.ainvoke(...)`.
The [complete examples](https://github.com/RamazanKara/agentworkflows/blob/main/sdk/python/agentworkflows/examples/frameworks.py)
show all four, including a governed Agents SDK function tool.

Each model/tool request gets its own step suffix and receipt. The activity result is
durable; a crash before completion can replay the **whole agent loop** and charge new
calls. Break long loops into separate workflow steps when finer recovery is needed.
Streaming, Responses, and background model APIs are not workflow step transports here;
use non-streaming Chat Completions or Anthropic Messages. Standalone gateway API support
is unchanged. The optional frameworks are pinned in the worker/test locks.

Framework callbacks run as trusted worker code. Route every side-effecting tool through
`context.tool`; arbitrary local function tools and direct network clients are not intercepted.
Use [container steps](agent-sandbox-integration.md#container-workflow-steps) for code execution.
Do not enable external LangSmith tracing with sensitive activity data.

## MCP tools

Register a server once under the team's existing `SandboxPolicySet` entry:

```yaml
sandboxId: research-team
mcpServers:
  team:
    url: https://tools.example.com/mcp
    credentialEnv: TEAM_MCP_TOKEN
    tools:
      search: 0.01
      publish: 0.02
```

The map explicitly approves remote tool names and their cost in USD per attempt. Clients
call `await gateway.tool("team.search", {"query": topic})` in workflow code or
`await context.tool(...)` in an agent callback. `GET /v1/tools` discovers team tools;
add run/step headers to see only that workflow's allowed tools. Credentials and server URLs
never come from agent arguments. Add the server's origin to the workflow's `allowedEgress`.

The gateway negotiates MCP **2025-03-26 Streamable HTTP**, initializes a session, invokes
`tools/call`, and closes the session. Both JSON and SSE responses are bounded by the gateway
body limit. Legacy SSE/stdio servers, resources, prompts, sampling, and server-initiated
requests are not supported. The registered allowlist is authoritative; discovery from an
untrusted server cannot grant another tool. The gateway does not execute model-returned
tool instructions automatically. See the [MCP transport specification](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports).

Nested JSON arguments use the existing input size, blocked-term, and secret policies before
any server contact. Set `PROMPT_SECRET_MODE=block` (the Compose default) to prevent disclosure;
`redact` rewrites matching arguments. Output follows the existing output DLP policy. Denied,
successful, and failed calls receive run-linked tool receipts, with argument fingerprints
rather than raw arguments. MCP errors and `isError` results are failures, never successful
results. Failed/ambiguous attempts retain their cost. For side effects, the server **must**
persist and honor `Idempotency-Key`; MCP itself does not promise idempotent execution.

## Workflow policy

Define policy under the same team entry, keyed by the registered Temporal workflow type:

```yaml
workflows:
  Briefing:
    allowedProviders: [openai, anthropic]
    allowedModels: [approved-openai, approved-anthropic]
    allowedTools: [team.search]
    allowedEgress: [https://api.openai.com, https://api.anthropic.com, https://tools.example.com]
    tokenLimit: 4000
    costLimitUsd: 1
```

Use canonical gateway model IDs, provider names from the model catalog, and exact URL origins
(scheme, hostname, and optional port; no paths or wildcards). An empty allowlist denies that
capability. The existing team model/tool rules still apply. Every fallback must satisfy the
workflow's provider, model, and egress lists. Container direct egress is restricted to DNS
and the gateway; `allowedEgress` governs the gateway's upstream model/tool connections.

`GET /v1/workflow-policies` shows the authenticated team's policies and approved container
agents. The SDK sends Temporal's workflow type when initializing the run. Once a team defines
workflow policies, unknown workflow types are refused. Existing teams without a `workflows`
map retain Milestone 2 behavior. Limits requested by the SDK are capped by configured limits;
the run's effective budget and workflow binding cannot change on retry. Current allowlists
are checked on every call; removing a bound policy revokes access. After changing budget caps,
start a reviewed new run rather than changing an existing run's limits.

Workers and team keys are trusted to select the correct workflow and run IDs. Restrict who
can submit code to those workers; these headers are correlation, not proof of Temporal identity.
Container step credentials cannot change their team/run binding or initialize another run.

## Run accounting

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
authorization, and TLS. The gateway verifies approval identities; direct Temporal clients
can bypass that check and must be restricted to operators and workers. Use separate Temporal
namespaces/deployments for stronger trust boundaries, and restrict signals and updates.

| Symptom | Next action |
| --- | --- |
| Run stays queued | Check worker logs, task queue, namespace, and `TEMPORAL_ADDRESS` |
| `workflow_store_required` | Set `SANDBOX_BUDGET_BACKEND=redis`, configure its URL, and enable persistence |
| `workflow_price_missing` | Add input/output prices to that model and every fallback |
| `workflow_*_budget_exceeded` | Inspect the run's usage; start a new reviewed run with an appropriate budget |
| `tool_not_allowed` | Inspect `GET /v1/tools` and the bound team's sandbox policy |
| `workflow_not_allowed` / `workflow_model_denied` | Inspect `GET /v1/workflow-policies`; use the registered workflow type and an allowed canonical model/provider |
| `workflow_egress_denied` | Add the reviewed tool server origin to that workflow's `allowedEgress` |
| `mcp_protocol_unsupported` | Use Streamable HTTP with MCP 2025-03-26 tool support |
| `workspace_busy` / `agent_failed` | Wait for the previous step to finish, or inspect the workspace before explicitly starting another run |
| Approval cannot be submitted | Wait for `awaiting_approval`; use an approver/admin key and the exact run ID |
