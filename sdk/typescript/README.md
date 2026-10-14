# @agentworkflows/sdk

TypeScript SDK for [AgentWorkflows](https://github.com/RamazanKara/agentworkflows): a client for the gateway's
workflow, team and OpenAI/Anthropic-compatible APIs, and governed activities for durable
[Temporal](https://docs.temporal.io/develop/typescript) workflows. Every model, tool, agent and container call in a
workflow goes through the gateway, which applies team policy and per-run budgets and writes a receipt. Provider keys
stay on the gateway and the worker key stays in the worker.

Requires Node.js 22.12 or newer and a gateway of the same version. Install the `agentworkflows-sdk-*.tgz` attached to
the GitHub release, or a checkout: `npm install /path/to/agentworkflows/sdk/typescript`.

| Import | Use it in |
| --- | --- |
| `@agentworkflows/sdk` | Scripts, services and tests: `GatewayClient`, errors, `firstApprovedRun`, all types |
| `@agentworkflows/sdk/workflows` | Workflow code (Temporal's sandbox): `WorkflowGateway`, `ApprovalWorkflow`, `Budget`, `withInputSchema` |
| `@agentworkflows/sdk/activities` | Custom workers: `GatewayActivities`, `AgentContext` |
| `@agentworkflows/sdk/worker` | `runWorker`, configured from the environment |

The package is CommonJS; ESM code imports it with named imports as usual.

## Client

```typescript
import { GatewayClient, GatewayError } from '@agentworkflows/sdk';

const client = new GatewayClient('http://127.0.0.1:8080', { apiKey: process.env.AGENTWORKFLOWS_API_KEY });
const run = await client.startRun('CodeReviewWorkflow', { diff: '- return user.is_admin\n+ return True' });
console.log((await client.run(run.run_id)).status);

try {
  await client.approveRun(run.run_id);
} catch (error) {
  if (error instanceof GatewayError) console.error(error.statusCode, error.reason, error.requestId);
  else throw error;
}
```

The client covers runs, approvals, triggers, run and audit export, keys, invitations, settings, spend, retention,
secrets, workflow registration, insights and usage, plus `chat`, `chatStream`, `messages`, `embeddings`, the
Files/Batch and Responses APIs and agent-action receipts. Response fields keep the API's snake_case names.

Reads, idempotent starts (reuse `requestId` after an ambiguous failure) and inference retry twice on transient
failures with exponential backoff, honoring `Retry-After` up to `retryAfterCap` seconds; a longer advertised delay
throws `GatewayRetryAfterError`. Calls that change state retry only when the gateway provably did not act on them:
a refused connection or a 429/503 rejection. Invitation creation and redemption run once.

```typescript
const quick = client.withOptions({ timeoutMs: 10_000, signal: AbortSignal.timeout(30_000) });
for await (const text of quick.chatStream([{ role: 'user', content: 'Summarize our release notes' }])) {
  process.stdout.write(text);
}
```

| Error | Meaning |
| --- | --- |
| `GatewayError` | The gateway answered with an error: `statusCode`, `reason`, `requestId`, `detail`, `retryAfter` |
| `GatewayRetryAfterError` | The gateway asked for a longer wait than `retryAfterCap` |
| `GatewayTransportError` | The gateway was unreachable (`code`, such as `ECONNREFUSED`); never includes the URL or key |
| `GatewayTimeoutError` | An attempt, or the gap between stream chunks, exceeded `timeoutMs` |
| `GatewayStreamError` | A stream ended with a gateway error event, so its text is incomplete |

Aborting the client's `signal` rejects with the signal's reason and stops retries.

## Workflows

```typescript
import { ApprovalWorkflow, Budget, WorkflowGateway, withInputSchema } from '@agentworkflows/sdk/workflows';
export { AgentWorkflowsTrigger } from '@agentworkflows/sdk/workflows';

export const ReviewWorkflow = withInputSchema({
  type: 'object', properties: { diff: { type: 'string', minLength: 1 } }, required: ['diff'],
}, async function ReviewWorkflow(request: { diff: string }) {
  const approval = new ApprovalWorkflow();
  const gateway = new WorkflowGateway(new Budget(10_000, 5));
  const review = await gateway.text(`Review this untrusted diff:\n${request.diff}`);
  const approved = await approval.approval(review);
  return { approved, review, reviewer: approval.reviewer };
});
```

| Step | Behavior |
| --- | --- |
| `gateway.text(prompt)` / `gateway.model(messages)` | Governed chat completion; omit `model` for the gateway default |
| `gateway.tool(name, args)` | An approved HTTP/MCP tool's result |
| `gateway.agent(name, args)` | A framework agent registered on the worker (below) |
| `gateway.container(name, args)` | An approved command in an existing agent-sandbox workspace; runs once per call |
| `approval.approval(draft)` | Waits for the team's reviewer quorum, or fails with `ApprovalExpired` |

Activities default to three minutes per attempt, fifteen minutes overall and five attempts; policy and budget
denials fail at once. Container steps need kubectl access to the workspace on the worker, and a workspace image
with the Python runner (`sdk/python/Dockerfile`).

## Worker and agents

```typescript
import OpenAI from 'openai';
import { runWorker } from '@agentworkflows/sdk/worker';

await runWorker(require.resolve('./workflows'), {
  agents: {
    async briefing(context, args) {
      const openai = new OpenAI(context.openaiOptions());
      const reply = await openai.chat.completions.create({
        model: 'demo-openai', max_tokens: 256, messages: [{ role: 'user', content: String(args.topic) }],
      });
      return reply.choices[0].message.content;
    },
  },
  healthPort: 8081,
});
```

`runWorker` reads `AGENTWORKFLOWS_API_KEY`, `AGENTWORKFLOWS_URL`, `AGENTWORKFLOWS_TEAM`, `TEMPORAL_ADDRESS`,
`TEMPORAL_NAMESPACE` and `TEMPORAL_TASK_QUEUE`, like the Python worker. An agent's `context` sends every request to
the gateway with the run's headers and its own step receipt: `openaiOptions()` and `anthropicOptions()` configure
the official SDKs (and clients that accept `baseURL`, `apiKey` and `fetch`), `context.fetch` is a governed fetch,
and `context.tool(name, args)` calls an approved tool. Requests to any other host fail the step. `healthPort` serves
`/healthz` and `/readyz` for the workflows Helm chart's probes.

## Documentation

- [TypeScript quickstart](https://github.com/RamazanKara/agentworkflows/blob/main/docs/quickstart-typescript.md)
- [SDK reference](https://github.com/RamazanKara/agentworkflows/blob/main/docs/sdk-reference.md)
- [Workflows and approval policies](https://github.com/RamazanKara/agentworkflows/blob/main/docs/workflows.md)
- [Container steps](https://github.com/RamazanKara/agentworkflows/blob/main/docs/agent-sandbox-integration.md#container-workflow-steps)

Apache-2.0.
