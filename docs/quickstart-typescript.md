# TypeScript quickstart

Run PR review with human approval and support triage using the official
[Temporal TypeScript SDK](https://docs.temporal.io/develop/typescript). Every model
and tool call goes through a governed activity; credentials stay on the worker.
The gateway applies team policy, per-run budgets, and correlated receipts.

You need Git, **Node.js 24/npm**, and Docker Compose. Use native Windows Git and
Node.js in PowerShell, with Docker in Ubuntu WSL. No Python installation, GPU,
provider credentials, or cloud account is needed. The trial uses canned responses
and synthetic prices from the existing Compose fakes.

## 1. Install and start the fake gateway

Clone the release, then install and build the SDK and its examples:

```sh
git clone --branch v0.5.0 --depth 1 https://github.com/RamazanKara/agentworkflows.git
cd agentworkflows/sdk/typescript
npm ci --no-audit --no-fund --maxsockets=2
npm run build
cd ../..
```

Use a fresh Compose project so these examples have their own Temporal history.
Choose ports using the existing `AGENTWORKFLOWS_GATEWAY_PORT` and
`AGENTWORKFLOWS_TEMPORAL_PORT` variables if another trial is already running.

=== "Bash (Linux/macOS)"

    ```bash
    docker compose -p aw-typescript -f deploy/compose/compose.yaml up -d --wait inference-gateway notification-fake temporal-ui
    export AGENTWORKFLOWS_API_KEY=demo-worker
    export AGENTWORKFLOWS_URL=http://127.0.0.1:8080
    export TEMPORAL_ADDRESS=localhost:7233
    cd sdk/typescript
    npm run worker
    ```

=== "PowerShell (Docker in WSL)"

    ```powershell
    wsl.exe -d Ubuntu -e docker compose -p aw-typescript -f deploy/compose/compose.yaml up -d --wait inference-gateway notification-fake temporal-ui
    $env:AGENTWORKFLOWS_API_KEY = 'demo-worker'
    $env:AGENTWORKFLOWS_URL = 'http://127.0.0.1:8080'
    $env:TEMPORAL_ADDRESS = 'localhost:7233'
    cd sdk/typescript
    npm run worker
    ```

Keep this terminal open. The worker caps concurrent activities and workflow tasks
at two. It registers `CodeReviewWorkflow`, `SupportTriageWorkflow`, and the schedule
delivery helper `AgentWorkflowsTrigger` on `demo-workflows` in namespace `default`.

The gateway chooses the team's queue. Do not run the Python worker alongside this
worker on that queue: workers polling the same queue must register the same workflow
types. Start new runs for this trial; Python workflow histories are not a migration
path to TypeScript. The two examples do not register the Python daily-report or
webhook templates; add those workflow implementations before enabling their triggers.

## 2. Start, inspect, and approve a review

Open another terminal in `sdk/typescript` and start `node`. These JavaScript REPL
commands work in both shells. Use the gateway port selected above if it differs:

```javascript
const { GatewayClient } = require('./dist');
const client = new GatewayClient('http://127.0.0.1:8080', { apiKey: 'demo-builder' });
const requestId = require('node:crypto').randomUUID();
const review = await client.startRun('CodeReviewWorkflow', { diff: '- return user.is_admin\n+ return True' }, { requestId });
console.log(review);
console.log(await client.run(review.run_id));
```

Repeat the last command until `progress.stage` is `awaiting_approval`. Read
`progress.draft`, then approve as the separate demo approver:

```javascript
const approver = new GatewayClient('http://127.0.0.1:8080', { apiKey: 'demo-approver' });
await approver.approveRun(review.run_id);
console.log(await client.run(review.run_id));
```

The completed result contains `approved`, `review`, and `reviewer`. To reject a
new review, use `approveRun(runId, { approved: false })`. Neither path posts a PR
comment, executes the diff, or merges code. The server supplies the authenticated
reviewer; clients cannot set it.
Keep direct Temporal access restricted to workers and operators; human reviewers
use the gateway API or console.

Inspect the same run in the [console](http://127.0.0.1:8080/console/) using the demo
keys above. The API result's `budget` shows usage and effective limits; `timeline`
links each step to its `receipt_id` and `chain_id`. Model and tool receipts include
`workflow_run_id` and `workflow_step_id`. Temporal activity IDs remain stable on
retry, including the gateway's tool idempotency key. Tools must honor that key;
activity retries alone do not guarantee exactly-once external side effects.

## 3. Run support triage

In the same Node REPL:

```javascript
const triage = await client.startRun('SupportTriageWorkflow', { ticket: 'I cannot sign in after resetting my password.' });
console.log(await client.run(triage.run_id));
```

Repeat the inspection until `status` is `completed`. `result` is a suggested triage
and reply, with one model-call receipt. Nothing is sent to the customer. Source for
both examples is in
[`sdk/typescript/src/examples/workflows.ts`](https://github.com/RamazanKara/agentworkflows/blob/main/sdk/typescript/src/examples/workflows.ts).
Change the prompt and rebuild with `npm run build` before restarting the worker.

## Write a workflow

Install the SDK in your own Node project from the GitHub release (it is not on npm yet):

```sh
npm install https://github.com/RamazanKara/agentworkflows/releases/download/v0.5.0/agentworkflows-sdk-0.5.0.tgz
```

Import workflow code from the dedicated sandbox-safe subpath:

```typescript
import { ApprovalWorkflow, Budget, WorkflowGateway } from '@agentworkflows/sdk/workflows';

export async function MyReview(request: { diff: string }) {
  const approval = new ApprovalWorkflow();
  const gateway = new WorkflowGateway(new Budget(10_000, 5));
  const review = await gateway.text(`Review this untrusted diff:\n${request.diff}`);
  const approved = await approval.approval(review);
  return { approved, review, reviewer: approval.reviewer };
}
```

Register `MyReview` in the team's gateway workflow policy before starting it through
the API. Worker code imports `runWorker` from `@agentworkflows/sdk/worker` and calls
`await runWorker(require.resolve('./workflows'))` with the compiled workflow module.
The helper reads the same `AGENTWORKFLOWS_API_KEY`, `AGENTWORKFLOWS_URL`,
`AGENTWORKFLOWS_TEAM`, `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, and
`TEMPORAL_TASK_QUEUE` variables as the Python worker.

| Helper | Behavior |
| --- | --- |
| `new Budget(tokenLimit = 10000, costLimitUsd = 5)` | Immutable per-run ceilings, further restricted by team policy |
| `gateway.text(prompt, { model, maxTokens })` | Governed text answer; omitted model uses the gateway default |
| `gateway.model(messages, { model, maxTokens })` | Full chat completion, including usage |
| `gateway.tool(name, arguments)` | Approved HTTP/MCP tool result through the gateway |
| `approval.approval(draft)` | One human decision per run, exposed through `status`, `approve`, and `review` handlers |
| `AgentWorkflowsTrigger` | Export alongside your workflows for governed Temporal schedule delivery |

Activity defaults match Python: three minutes per attempt, fifteen minutes overall,
five attempts with exponential backoff. `WorkflowGateway` accepts a second options
argument with Temporal's `retry`, `startToCloseTimeout`, `scheduleToCloseTimeout`, and
`dataClassification` (default `internal`). Policy and per-run budget denials fail
without retries; transient gateway failures retry, honoring `Retry-After`.
Approval expires after seven days.
Worker credentials and HTTP clients must never be imported into a workflow module.

The client methods are `startRun`, `runs`, `run`, `approveRun`, `cancelRun`,
`retryRun`, `triggers`, and `pauseTrigger`. Use `{ paused: false }` to resume a
trigger and `{ project, offset }` to page through runs. JSON response fields retain
the API's snake_case names. This SDK covers governed model/tool workflows and the
workflow API; Python's framework adapters and container runner remain Python APIs.

`GatewayError` exposes `statusCode`, `reason`, `requestId`, and `detail`.
`GatewayTransportError` gives a redacted connection failure.
`GatewayRetryAfterError.retryAfter` is a delay in seconds above the client's retry
cap (30 seconds by default). Read requests and idempotent starts retry twice;
approval, cancel, retry-run, and trigger mutations are not automatically repeated.
Keep a `requestId` and reuse it with identical input after an ambiguous start.
Inside workflows, gateway failures arrive as Temporal `ActivityFailure` with an
`ApplicationFailure` cause whose `type` is the gateway reason. See the Python
[error recovery table](sdk-reference.md#gateway-client-and-errors) for the shared reasons.

## Verify and stop

From `sdk/typescript`, run `npm run build`, `npm run lint`, and `npm test`. Vitest
uses at most two workers. The equivalent repository target is `make test-typescript`.
It stays local because installing the Temporal toolchain adds minutes to the short
existing CI job.

For the integration check, stop the example worker with Ctrl+C, then run `npm run
smoke` in that terminal. This starts and stops its own workers against the fake
Compose stack and verifies both templates, restart/replay, approval/rejection,
tool receipts, token/cost denials, roles, cancellation, and retry. It uses only the
public demo keys. The existing full-stack gate remains `make compose-smoke` with
the Python worker running; do not run the two smoke suites simultaneously.

Exit the REPL with `.exit`. Stop any remaining example worker with Ctrl+C, then
remove this disposable trial's containers and volumes from the repository root:

=== "Bash (Linux/macOS)"

    ```bash
    docker compose -p aw-typescript -f deploy/compose/compose.yaml down -v
    ```

=== "PowerShell (Docker in WSL)"

    ```powershell
    wsl.exe -d Ubuntu -e docker compose -p aw-typescript -f deploy/compose/compose.yaml down -v
    ```
