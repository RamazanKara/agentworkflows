# TypeScript quickstart

Run PR review with human approval and support triage using the official
[Temporal TypeScript SDK](https://docs.temporal.io/develop/typescript). Every model
and tool call goes through a governed activity; credentials stay on the worker.
The gateway applies team policy, per-run budgets, and correlated receipts.

You need Git, **Node.js 24/npm**, and Docker Compose. Use native Windows Git and
Node.js in PowerShell, with Docker in Ubuntu WSL; that is everything this trial uses.
The trial uses canned responses and synthetic prices from the Compose fakes.

## 1. Install and start the fake gateway

Use a v0.9.1 checkout, then install and build the SDK and its examples. Skip clone/cd if
you already have the repository checkout. Compose pulls the published v0.9.1 gateway image:

```sh
git clone --branch v0.9.1 --depth 1 https://github.com/RamazanKara/agentworkflows.git
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

The gateway chooses the team's queue. Run this worker on its own on that queue:
workers polling the same queue register the same workflow types. Start new runs for
this trial. To enable the Python daily-report or webhook triggers here, add those
workflow implementations to this worker first.

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
new review, use `approveRun(runId, { approved: false })`. Both decisions finish the
run with the review as its result. The server supplies the authenticated reviewer.
Keep direct Temporal access restricted to workers and operators; human reviewers
use the gateway API or console.

Inspect the same run in the [console](http://127.0.0.1:8080/console/) using the demo
keys above. The API result's `budget` shows usage and effective limits; `timeline`
links each step to its `receipt_id` and `chain_id`. Model and tool receipts include
`workflow_run_id` and `workflow_step_id`. Temporal activity IDs remain stable on
retry, including the gateway's tool idempotency key. Have tools honor that key so
each external side effect applies once across retries.

## 3. Run support triage

In the same Node REPL:

```javascript
const triage = await client.startRun('SupportTriageWorkflow', { ticket: 'I cannot sign in after resetting my password.' });
console.log(await client.run(triage.run_id));
```

Repeat the inspection until `status` is `completed`. `result` is a suggested triage
and reply, with one model-call receipt. The reply stays in the run result for your
team to send. Source for
both examples is in
[`sdk/typescript/src/examples/workflows.ts`](https://github.com/RamazanKara/agentworkflows/blob/main/sdk/typescript/src/examples/workflows.ts).
Change the prompt and rebuild with `npm run build` before restarting the worker.

## Write a workflow

Install the built SDK in your own Node project from your checkout. Replace the
path below with your checkout path (a local install; the release also attaches
`agentworkflows-sdk-0.9.1.tgz`):

```sh
npm install /path/to/agentworkflows/sdk/typescript
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
the API, or with `registerWorkflow(MyReview, models)` from an admin client (a function
declared with `withInputSchema` also sends its schema). Worker code imports `runWorker`
from `@agentworkflows/sdk/worker` and calls
`await runWorker(require.resolve('./workflows'))` with the compiled workflow module.
The helper reads the same `AGENTWORKFLOWS_API_KEY`, `AGENTWORKFLOWS_URL`,
`AGENTWORKFLOWS_TEAM`, `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, and
`TEMPORAL_TASK_QUEUE` variables as the Python worker. Its second argument takes
`agents` (below) and `healthPort`. The `/healthz` and `/readyz` probes the workflows
Helm chart expects listen on `healthPort`, or on `AGENTWORKFLOWS_HEALTH_ADDRESS`
(`host:port`, such as `0.0.0.0:8081`). Unlike the Python worker, the TypeScript worker
opens no port when neither is set, so a local trial needs no firewall exception. A
failed Temporal connection or a taken health port names the setting to change.

| Helper | Behavior |
| --- | --- |
| `new Budget(tokenLimit = 10000, costLimitUsd = 5)` | Immutable per-run ceilings, further restricted by team policy |
| `gateway.text(prompt, { model, maxTokens })` | Governed text answer; omitted model uses the gateway default |
| `gateway.model(messages, { model, maxTokens })` | Full chat completion, including usage |
| `gateway.tool(name, arguments)` | Approved HTTP/MCP tool result through the gateway |
| `gateway.agent(name, arguments)` | Result of a framework agent registered on the worker |
| `gateway.container(name, arguments)` | Output of an approved command in an agent-sandbox workspace; runs once per call |
| `approval.approval(draft)` | Policy-driven reviewer quorum and expiry, exposed through `status`, `approve`, and `review` handlers |
| `AgentWorkflowsTrigger` | Export alongside your workflows for governed Temporal schedule delivery; it reads the target run's status after 30 seconds, backs off to every five minutes, keeps only the run's ID and status in history, and continues as new when Temporal suggests it |

To widen who may review a run that waits too long, an admin calls
`setApprovalEscalation(workflow, afterSeconds, { approver_role, reviewers, channels })`
(revision-checked like the other team settings; `approvalEscalations()` lists them and
`removeApprovalEscalation(workflow)` stops new runs escalating). Reviewers iterate
`for await (const run of client.reviewQueue())` over the waiting runs they may review now;
each run's `approval_access` and `approval_escalation` say why and when. See
[approval escalation](workflows.md#approval-escalation).

Activity defaults match Python: three minutes per attempt, fifteen minutes overall,
five attempts with exponential backoff. `WorkflowGateway` accepts a second options
argument with Temporal's `retry`, `startToCloseTimeout`, `scheduleToCloseTimeout`, and
`dataClassification` (default `internal`). Policy and per-run budget denials fail
without retries; transient gateway failures retry, honoring `Retry-After`.
Approval defaults to one reviewer and seven days; a run snapshots the team's quorum
and expiry policy when it starts.
Keep worker credentials and HTTP clients out of workflow modules.

The workflow client methods are `startRun`, `runs`, `run`, `approveRun`, `cancelRun`,
`retryRun`, `triggers`, and `pauseTrigger`. Use `{ paused: false }` to resume a
trigger and `{ project, cursor }` to page through runs. JSON response fields retain
the API's snake_case names. The client also mirrors the Python client's inference
calls: `chat`, `chatStream`, `completions`, `embeddings`, `moderations`, `batch`
and `messages`; the Files/Batch API (`uploadBatchFile`, `createBatch`, `getBatch`,
`getFileContent`, ...); the Responses API (`createResponse`, `getResponse`, ...);
`recordReceipt`; and `team`, `usage`, `sandboxBudget` and `ready`. Request fields
beyond the messages pass through with the API's names, for example
`client.chat(messages, { model: 'demo-openai', max_tokens: 64 })`.

`GatewayError` exposes `statusCode`, `reason`, `requestId`, and `detail`.
`GatewayTransportError` gives a redacted connection failure with the system `code`
(such as `ECONNREFUSED`); its subclass `GatewayTimeoutError` reports an attempt that
exceeded `timeoutMs` (120 seconds by default). `GatewayStreamError` means a stream
ended with a gateway error event, so its text is incomplete.
`GatewayRetryAfterError.retryAfter` is a delay in seconds above the client's retry
cap (30 seconds by default). Read requests, idempotent starts and inference retry
twice. Approval, cancel, retry-run, trigger, key, settings and other mutations
retry only when the gateway provably did not act: the connection was refused or
the gateway answered 429 or 503. Invitation creation and redemption run once.
Keep a `requestId` and reuse it with identical input after an ambiguous start.
Inside workflows, gateway failures arrive as Temporal `ActivityFailure` with an
`ApplicationFailure` cause whose `type` is the gateway reason. See the Python
[error recovery table](sdk-reference.md#gateway-client-and-errors) for the shared reasons.

Client options also take `sandboxId`, default `headers` (for example `traceparent`),
`retryBackoffMs`, a custom `fetch`, and a `signal`. `withOptions` returns a copy for
one call; aborting its signal rejects with the signal's reason and skips retries:

```typescript
const reply = await client.withOptions({ signal: AbortSignal.timeout(10_000) })
  .chat([{ role: 'user', content: 'Summarize the incident' }]);
```

## Framework agents

Run an OpenAI, Anthropic or other framework agent as one governed workflow step.
Register it on the worker and call it from the workflow with
`await new WorkflowGateway().agent('briefing', { topic })`:

```typescript
import OpenAI from 'openai';
import { runWorker } from '@agentworkflows/sdk/worker';

await runWorker(require.resolve('./workflows'), {
  agents: {
    async briefing(context, args) {
      const sources = await context.tool('team.search', { query: args.topic });
      const openai = new OpenAI(context.openaiOptions());
      const reply = await openai.chat.completions.create({
        model: 'demo-openai', max_tokens: 128,
        messages: [{ role: 'user', content: `Summarize: ${JSON.stringify(sources)}` }],
      });
      return reply.choices[0].message.content;
    },
  },
});
```

Install the framework packages in your worker project; the SDK does not depend on
them. `context.openaiOptions()` and `context.anthropicOptions()` return `baseURL`,
`apiKey`, a governed `fetch` and `maxRetries: 0` for the official SDKs and for
clients that accept the same options. `context.fetch` is that governed fetch and
`context.tool(name, args)` calls an approved tool. Every request carries the run,
classification and its own step ID (`<step>/<n>`), so each gets a receipt. Requests
to any host other than the gateway fail the step with `AgentEgressDenied`. A policy
or budget denial fails the step without retries, even when the framework wraps the
error. Custom workers pass the same `agents` map to
`new GatewayActivities(url, key, { agents })`. As in Python, a crash mid-agent reruns
the whole agent function; see [Bring your agent](workflows.md#bring-your-agent).

## Container steps

`await new WorkflowGateway().container('coder', { task })` runs the command the team
policy approved for that agent in its existing agent-sandbox workspace and returns the
output. It runs once per call. The worker checks the Sandbox pod and network policies
exactly as the Python worker does, passes the short-lived grant to the workspace runner
on stdin only, and always revokes the grant. The worker needs `kubectl` with access to
that workspace; the workspace image needs the Python runner from `sdk/python/Dockerfile`.
See [container workflow steps](agent-sandbox-integration.md#container-workflow-steps)
for the policy, chart and RBAC setup.

## Team administration

Use an admin key; settings and audit require
a credential without a project restriction.

| Method | Typed result / behavior |
| --- | --- |
| `listKeys()` | `KeyList`, including revoked and expired keys |
| `createKey(name, { role, project, expires_at })` | `CreatedKey`; save the one-time plaintext `key`; options are optional and role defaults to viewer |
| `updateKey(keyId, { name, role, project, expires_at })` | `ManagedKey`; omitted fields stay unchanged; `null` clears project or expiry |
| `revokeKey(keyId)` | Revoked `ManagedKey`; use a different key to revoke the current one |
| `teamSettings()` | `TeamSettings`: revision, update metadata, effective fields with value/source/policy_default and available route/provider/approver choices |
| `updateTeamSettings(fields, { revision })` | Atomically apply a field/value map and return `TeamSettings` |
| `resetTeamSetting(field, { revision })` | Remove one override and return `TeamSettings` |
| `audit({ from, to, eventType, actor, project, runId, cursor, limit })` | One newest-first `AuditPage` with original events, chain metadata and `next_cursor`; all options optional |
| `verifyAudit({ from, to })` | `AuditVerification`: enabled, ok, checked, first_break and boundaries; `ok` is null when disabled |
| `exportAudit(filters)` | Async generator of newline-terminated JSON strings, one original event per line, following all matching cursor pages |

The package exports the request/response types `Role`, `KeyOptions`, `KeyUpdate`,
`ManagedKey`, `CreatedKey`, `KeyList`, `TeamSettingValue`, `TeamSetting`,
`TeamSettings`, `AuditRange`, `AuditFilters`, `AuditEntry`, `AuditPage`,
`AuditPosition`, and `AuditVerification`. Key input expiry is an ISO-8601 string
with a timezone. Response timestamps are Unix seconds.

Read and review settings before writing. Pass the returned integer revision (or
a quoted ETag string) to either mutation; the SDK sends it as `If-Match`. A stale
write throws `GatewayError` with `statusCode: 409` and
`reason: 'team_settings_conflict'`. Reload, review and reapply the change with the
new revision. A 422 rejects the whole patch and
retains `detail.fields` entries containing `field` and `message`. See
[team settings](team-settings.md) for field names and limits.

```typescript
import { GatewayClient } from '@agentworkflows/sdk';
import { open } from 'node:fs/promises';

const admin = new GatewayClient('http://127.0.0.1:8080', {
  apiKey: process.env.AGENTWORKFLOWS_API_KEY,
});
const settings = await admin.teamSettings();
const saved = await admin.updateTeamSettings({ cost_limit_usd: 100 }, { revision: settings.revision });
await admin.resetTeamSetting('cost_limit_usd', { revision: saved.revision });

const output = await open('audit-log.jsonl', 'w');
try {
  for await (const line of admin.exportAudit({ eventType: 'team_settings_changed' })) {
    await output.write(line, undefined, 'utf8');
  }
} finally {
  await output.close();
}
```

Audit times are inclusive Unix seconds. List/export filters match exactly;
`limit` is the page size (default 50, maximum 200). Supply the previous page's
`next_cursor` as `cursor` for older events. Export fixes `to` at the start time
when omitted and preserves all filters across pages. Verification accepts only
time bounds and includes events hidden by other filters. Check `enabled` on list
and verification results; a disabled view has `ok: null`, and export throws an
`Error` with the gateway's message. Gateway read failures propagate to the caller.
Retention continues during paging; the operator verifier reads the complete process
logs. See [audit log](audit-log.md)
for range boundaries and what verification proves.

## Run history and export

Use the gateway from the [Python quickstart](quickstart.md#1-install-and-start)
and the v0.9.1 SDK built from `sdk/typescript`. `runs({ project, workflow, status, cursor, limit })`
returns a typed `RunPage`. All filters are optional. Pass `next_cursor` with the
same project and filters until it is null, including on empty pages. `limit`
bounds scanned records before filtering (1–100, default 20). Cursors remain stable
when newer runs arrive or records expire; legacy `offset` remains supported.

`exportRuns(filters)` yields newline-terminated JSON strings containing each
retained run's full details, results and timeline, including captured content
still retained by policy. It follows every cursor page. The package exports
`RunFilters` and `RunPage`.

```typescript
const gateway = new GatewayClient('http://127.0.0.1:8080', {
  apiKey: process.env.AGENTWORKFLOWS_API_KEY,
});
const output = await open('runs.jsonl', 'w');
try {
  for await (const line of gateway.exportRuns({ status: 'completed' })) {
    await output.write(line, undefined, 'utf8');
  }
} finally {
  await output.close();
}
```

As in the audit example, import `GatewayClient` and `open` above. Gateway and
transport errors propagate to the caller. Retention and status changes continue
during export, so the file reflects the runs retained at read time; the stored start
input stays out of the export. Give downloaded files their own retention policy. See [CLI and SDK paging](sdk-reference.md#cli).

## Company group access

With an unrestricted team-admin credential,
`await client.teamSSO()` inspects the same sign-in policy shown in Members & keys.
See the [typed contract and example](sdk-reference.md#company-group-access).
Configure your existing identity provider using the
[group mapping guide](workflows.md#sso-group-to-role-mapping); the local fixture
workflow signs in with API keys.

## Trigger history and usage CSV

Run history is available
from each console trigger; `runs({ workflow, trigger, project, cursor })` and
`exportRuns({ workflow, trigger })` use the same project-scoped filter. A trigger name
requires a workflow. Trigger provenance covers scheduled and webhook launches, and
run retention applies. Manual retries are separate runs.

`await gateway.exportUsage()` returns UTF-8 CSV for the current UTC month. Write it
with `writeFile('usage.csv', await gateway.exportUsage(), 'utf8')` from `node:fs/promises`,
or use **Costs → Export CSV**. The total and provider/workflow rows overlap and include
reservations. See the [column and scope reference](sdk-reference.md#trigger-history-and-usage-csv).

## Verify and stop

From `sdk/typescript`, run `npm run build`, `npm run lint`, and `npm test`. Vitest
uses at most two workers. The equivalent repository target is `make test-typescript`,
which runs locally to keep the CI job short. `npm run check:package` (also run by
`npm pack`) installs the packed tarball into a scratch project and checks its
contents, CommonJS and ESM imports of every entry point, and TypeScript resolution
under `node16`, `bundler` and legacy `node` module settings.

For the integration check, stop the example worker with Ctrl+C, then run `npm run
smoke` in that terminal. This starts and stops its own workers against the fake
Compose stack and verifies both templates, restart/replay, approval/rejection,
tool receipts, token/cost denials, roles, cancellation, and retry. It uses only the
public demo keys. The existing full-stack gate remains `make compose-smoke` with
the Python worker running; run the two smoke suites one at a time.

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


## Multi-reviewer approval

Use the v0.9.0 gateway and SDK worker before enabling
nondefault approval policies. In **Team settings / Code review**, set Required
reviewers to `2` and Approval expiry (seconds) to `3600`, then start a new review.
The first positive vote leaves it waiting; a second distinct reviewer completes the
quorum. Any rejection ends review, and expiry fails the run with `ApprovalExpired`.
Both SDKs use the same [approval contract](workflows.md#approval-policies).
