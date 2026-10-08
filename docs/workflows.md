# Durable agent workflows

AgentWorkflows uses [Temporal](https://docs.temporal.io/develop/python) to persist workflow
history, schedule activities, retry failures, and wait for signals. The Python SDK sends
every model and tool activity through the governed gateway. Provider credentials stay on
the gateway; each worker uses one team's bound gateway key.

## Try research → draft → approval → publish

Follow the [Quickstart](quickstart.md) for a complete fake or real-cloud run with human
approval and verified receipts. It includes Bash and Windows commands, SDK installation,
scaffolding, and cleanup. The [template gallery](templates.md) adds PR review, support triage,
weekly reports, incident summaries and document Q&A, with instructions for running your edits.

`approve --reject` finishes without publishing. Approval expires after seven days.
The API records the authenticated key name or verified JWT subject; clients cannot supply
a reviewer name. A Temporal update accepts one decision for the waiting draft. Early or
conflicting decisions return `409 approval_not_waiting`. Retrying the same identity's
decision is idempotent, including after a lost response. Run IDs identify exact executions.

The fake research draft deliberately falls back from OpenAI to Anthropic. Both tools are
local fixtures. `make compose-smoke` additionally kills and replaces the worker while
approval waits, then verifies no repeated completed model calls. See [local evaluation](local-evaluation.md).

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

Sign-in exchanges a team API key or signed JWT for a server-side Redis session. Reloading
restores the workspace; **Sign out** invalidates the session on every replica.
**Switch identity** replaces the active session after verifying another credential.
The browser retains an HttpOnly session cookie, never the API key or JWT.
When OIDC is configured, **Sign in with your provider** uses the company account instead.
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
bootstrap team policies and an admin with `SANDBOX_POLICY_PATH` and `API_KEY_RECORDS_PATH`
or verified identity-provider claims. Admins then issue and revoke member keys in
**Members & keys**, without editing YAML or restarting the gateway.
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
the trial. Keep a bootstrap admin record and add a policy for each team:

```yaml
# Entry under records in key-records.yaml; store only the random key's SHA-256 digest.
- name: alice
  sha256: REPLACE_WITH_SHA256_OF_RANDOM_KEY
  sandbox: research-team
  role: admin
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
team's existing credentials. Team policies remain declarative; membership keys are managed
in the console, CLI, or team key API.

### Members and API keys

Admins can list, create, update, and revoke keys for their own team. Project-bound admins
can only manage keys in that project. The console lists name, role, project, last use,
expiry, and revocation status. Creation displays the new `aw_` key once, with a copy button;
save it securely before leaving the page. The store retains only its SHA-256 digest and
metadata, including creator and timestamps. Last use is written at most once per minute.
Every create, update, and revoke produces a hash-chained audit receipt.

```bash
agentworkflows keys list
agentworkflows keys create --name alice --role builder --project briefing
agentworkflows keys update KEY_ID --role viewer --expires-at 2027-01-01T00:00:00Z
agentworkflows keys revoke KEY_ID
```

`GET/POST /v1/team/keys` list/create; `PATCH/DELETE /v1/team/keys/{key_id}` update/revoke.
POST accepts `name`, `role` (default `viewer`), optional `project`, and optional `expires_at`.
PATCH accepts those same fields; `null` clears project or expiry. Expiry accepts epoch seconds
or an ISO-8601 timestamp with a timezone. DELETE retains a revoked record. Revocation,
expiry, and role changes apply on the next request on every replica, including key-backed
browser sessions. An admin cannot revoke or demote the key authenticating their request.
File-based bootstrap credentials remain managed through the existing configuration.

The managed store uses the existing budget Redis client and key prefix, with schema version 2;
enable Redis persistence and back up this state along with the existing runs and budgets.
There is no managed-key feature flag. Existing flat hashes, key-record files, and JWT/JWKS
automation remain supported. The flat allowlist and file are checked before the managed
store, then JWT verification; a duplicate file record still enforces its binding and expiry
even when its digest is also flat-listed.

### Company sign-in (OIDC)

Set `ADMIN_CONSOLE_ENABLED=true` and `SANDBOX_BUDGET_BACKEND=redis`. Register an
authorization-code OIDC application with the exact gateway callback URL and configure:

```text
OIDC_ISSUER=https://company.okta.com/oauth2/default
OIDC_CLIENT_ID=your-client-id
OIDC_CLIENT_SECRET=your-client-secret
OIDC_REDIRECT_URL=https://agents.example.com/v1/auth/callback
OIDC_SCOPES=openid profile email
OIDC_TEAM_CLAIM=team
OIDC_ROLE_CLAIM=role
OIDC_PROJECT_CLAIM=project
OIDC_DEFAULT_ROLE=viewer
```

Source the client secret from your deployment's secret store. Omit it for a registered
public client. The gateway discovers authorization, token, and JWKS endpoints from the
issuer; it supports `client_secret_basic` and `client_secret_post` for confidential clients.
OIDC is disabled when its settings are absent. `OIDC_TEAM_CLAIM` defaults to
`JWT_TENANT_CLAIM`; the role/project defaults are `role`/`project`. Claims are top-level
strings. The team must name an existing sandbox policy, and any project must belong to it.
A missing role uses `OIDC_DEFAULT_ROLE`; an invalid supplied role is rejected. Only the
identity provider's administrators should be able to set membership claims.

In Helm, use `auth.oidc` for these settings and `auth.oidc.existingSecret.name/key` for
the client secret. `adminConsole.cookieSecure` defaults to true. The umbrella chart nests
these values under `inference-gateway`. Allow HTTPS egress to the provider's discovery,
token and JWKS endpoints in your existing network policy.

| Provider | Configuration |
| --- | --- |
| [Okta](https://developer.okta.com/docs/guides/customize-tokens-returned-from-okta/) | Use your authorization server's issuer, register the redirect URL, and emit `team`, `role`, and optional `project` in the **ID token**, including them for the requested scopes. |
| [Microsoft Entra](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc) | Use the tenant-specific issuer `https://login.microsoftonline.com/TENANT_UUID/v2.0`, register a Web redirect, and map `OIDC_TEAM_CLAIM=tid` to a policy whose sandbox ID is that tenant UUID. Missing roles default to viewer. For other roles, emit a single string custom claim; the `roles` array is not a scalar role claim. |
| [Google](https://developers.google.com/identity/openid-connect/openid-connect) | Use issuer `https://accounts.google.com` and an OAuth web client with the exact redirect URL. Direct Google ID tokens do not provide the gateway's team/role/project claims, and the dotted `hd` domain is not a valid sandbox ID. Use an OIDC broker that emits controlled membership claims, or keep team API-key sessions. |

The flow uses [PKCE S256](https://www.rfc-editor.org/rfc/rfc7636), single-use state bound
to the browser, and a nonce. ID tokens must pass signature, issuer, audience, expiry,
issued-at, subject, nonce, and authorized-party checks. Tokens and the client secret are
never sent to browser storage. Keep all replicas on the same Redis and auth configuration.

`GET /v1/auth/config` publicly describes available sign-in methods without secrets.
`GET /v1/auth/login` starts OIDC; `GET /v1/auth/callback` completes it.
`POST /v1/auth/session` with `{"key":"..."}` creates the same session from an API key
or existing JWT. `GET /v1/auth/session` restores it and returns the CSRF token;
`POST /v1/auth/logout` invalidates it. Browser sign-in is enabled by the console or OIDC
configuration and requires Redis. Sessions slide for 12 hours with an absolute seven-day
limit; pasted JWT sessions also stop at the JWT expiry. OIDC claim changes take effect on
the next sign-in. Logout ends the gateway session, not the provider's own login session.

Cookies are HttpOnly (session), Secure, and SameSite=Lax. The separate CSRF cookie must
match both the session's token and `X-CSRF-Token` on non-GET cookie-authenticated requests.
The console sends credentials and that header automatically. Bearer and `X-API-Key`
automation do not require CSRF. Serve the console and gateway on the same HTTPS origin.
For HTTP localhost only, set `SESSION_COOKIE_SECURE=false`; Compose already does so and
`local-development-only` continues to work. The demo adds no identity-provider container;
connect an existing provider using the settings above.

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
decision was accepted. ApprovalWorkflow implements both for the research and code-review templates.

## Triggers and notifications

Open **Triggers** in the console to see cron expressions, upcoming times, signed webhook
endpoints and pause state. Admins and builders can pause/resume triggers in their projects;
viewers and approvers can inspect them. Existing runs continue when a trigger is paused.

```bash
agentworkflows triggers list
agentworkflows triggers pause DailyReportWorkflow daily
agentworkflows triggers resume DailyReportWorkflow daily
```

Merge trigger definitions into each workflow in the existing `SandboxPolicySet`. Keep
its provider/model/tool/egress policy and budgets. For example:

```yaml
workflows:
  DailyReportWorkflow:
    allowedProviders: [openai]
    allowedModels: [demo-openai]
    allowedEgress: [http://cloud-fake:8000]
    triggers:
      daily:
        kind: cron
        project: default
        cron: "0 9 * * *"
        input: {topic: Agent operations}
  GitHubIssueTriageWorkflow:
    allowedProviders: [openai]
    allowedModels: [demo-openai]
    allowedEgress: [http://cloud-fake:8000]
    triggers:
      github:
        kind: webhook
        project: engineering
webhookSecretEnv: TEAM_WEBHOOK_SECRET
notifications:
  consoleUrl: https://agents.example.com/console/
  slackWebhookEnv: TEAM_SLACK_WEBHOOK
  webhookEnv: TEAM_OUTGOING_WEBHOOK
  budgetThreshold: 0.8
  smtp:
    host: smtp.example.com
    port: 587
    startTls: true
    usernameEnv: TEAM_SMTP_USERNAME
    passwordEnv: TEAM_SMTP_PASSWORD
    sender: workflows@example.com
    recipients: [team@example.com]
```

Store secrets in the gateway environment/Secret deployment. Use a separate randomly
generated webhook secret of at least 32 characters for each team. Webhook destination
variables hold full HTTP(S) URLs; values and recipient addresses are never put in receipts.
Only configure destinations your team is authorized to notify. Omit unused channels.
SMTP authentication requires TLS; only the local fake uses unauthenticated plaintext SMTP.
Set `consoleUrl` to the externally reachable console address, including any path prefix.
Notification links carry no credentials: recipients sign in normally.

Recreate the gateway after configuration changes. It reconciles
[Temporal Schedules](https://docs.temporal.io/develop/python/workflows/schedules), never a
custom cron scheduler. Expressions use UTC, overlap policy is **SKIP**, and catch-up is
limited to five minutes. The SDK worker registers the scheduled-launch workflow; it keeps
the schedule action open until the target run ends. Each launch goes through the same
governed start API. Redis persists interactive pauses across restarts; `paused: true` in
configuration prevents console/CLI resumption until the admin changes the configuration.
Removing a cron trigger removes its managed schedule on the next reconciliation. Schedule
errors appear in the console; check Temporal and the gateway log for invalid expressions.

For an inbound webhook, POST the **exact JSON bytes** to the endpoint shown in Triggers.
Send `X-AW-Timestamp` (Unix seconds, within five minutes), `X-AW-Delivery` (a unique ID using
letters, digits, underscores or hyphens, up to 128 characters) and `X-AW-Signature`:

```python
import hashlib, hmac, json, os, time, urllib.request, uuid

path = "/v1/hooks/demo/GitHubIssueTriageWorkflow/github"
body = json.dumps({"action": "opened", "issue": {
    "number": 42, "title": "Sign-in regression", "body": "Please investigate."
}}).encode()
stamp, delivery = str(int(time.time())), str(uuid.uuid4())
signed = f"{stamp}.{delivery}.{path}.".encode() + body
signature = hmac.new(os.environ["TEAM_WEBHOOK_SECRET"].encode(), signed, hashlib.sha256).hexdigest()
request = urllib.request.Request("http://127.0.0.1:8080" + path, data=body, headers={
    "Content-Type": "application/json", "X-AW-Timestamp": stamp,
    "X-AW-Delivery": delivery, "X-AW-Signature": "sha256=" + signature,
})
print(urllib.request.urlopen(request).read().decode())
```

Compose uses the public fake value `compose-webhook-secret-not-for-production` for this
example. The original JSON becomes the workflow input; it is not stored in receipts.
For GitHub, configure the shown endpoint as the repository webhook URL, use JSON content,
select Issues events, and set its secret to the team's webhook secret. Native GitHub
`X-Hub-Signature-256` plus `X-GitHub-Delivery` are also accepted. GitHub does not sign a
timestamp or delivery ID, so the gateway persists the signed body fingerprint across
the team: changing the delivery ID or destination cannot replay an accepted payload.
Identical GitHub payload bytes are treated as retries. No GitHub credential or live GitHub
write is used by the triage example.

Completed delivery IDs return `409 webhook_replayed`; concurrent delivery and ambiguous
Temporal failures use the same deterministic run ID. After a `503`, retry with the same
delivery ID/body and a fresh timestamp/signature. Changed input for that ID is refused.
Keep Redis trigger state, run start intents and Temporal history together in backups.
Deleting that state removes the replay guarantee. A paused trigger returns `409 trigger_paused`.

Notifications cover console-managed runs: waiting approval, failure (including execution
timeout), and reaching the configured fraction of either the run token or USD limit.
The existing run monitor checks roughly every 30 seconds; SDK approval events are persisted
so a quick review is not missed. Accounting includes conservative outstanding reservations.
Budget crossings are also persisted at reservation/settlement, even if usage subsequently falls.
Custom workflows should inherit `ApprovalWorkflow` (or expose the existing `status` query).
Already-running histories remain replay-compatible via a Temporal patch marker.

Each channel has persistent per-run/event delivery state and a replica-safe lease. Attempts
retry up to five times with exponential backoff, without blocking approval. Every attempt
and outcome appears in the run timeline; `retrying` or `failed` means inspect the destination
and gateway configuration. Successful deliveries are deduplicated across restarts. Delivery
is **at least once**, not exactly once: a crash after sending but before recording success
can duplicate a message. Outgoing webhooks carry a stable `Idempotency-Key` and JSON `id`;
SMTP uses a stable Message-ID. Notifications contain IDs, event type and a console link,
not drafts, prompts or raw failure details. Approvals remain in the console/API.

The Compose trial includes both workflows and `notification-fake`, a Slack/webhook sink
and SMTP catcher. View its inbox at `http://127.0.0.1:8025/` (override the published port
with `AGENTWORKFLOWS_INBOX_PORT`). `make compose-smoke` backfills a daily schedule through
Temporal, posts a signed issue payload, checks pause/replay protection, exercises approval
and failure delivery to all three channels, and verifies the receipt chain. It uses no
real credentials. Both examples return reviewed content without publishing to external services.

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
    async def run(self, topic: str) -> str:
        gateway = WorkflowGateway(Budget(token_limit=4000, cost_limit_usd=2.0))
        sources = await gateway.tool("research", {"query": topic})
        return await gateway.text(
            f"Summarize with citations: {sources}",
            model="demo-openai",
            max_tokens=512,
        )
```

Run it from a separate `worker.py` entry point (keep network I/O outside workflow modules):

```python
from agentworkflows.worker import run_worker
from workflow import Briefing

if __name__ == "__main__":
    run_worker([Briefing])
```

Set `AGENTWORKFLOWS_TEAM=research-team`, its worker gateway key, and the gateway/Temporal
addresses. Register `Briefing` in the team workflow policy, then start with
`agentworkflows runs start Briefing --input '"your topic"' --project briefing`.
Use `ApprovalWorkflow` and `await self.approval(draft)` for the standard reviewed-draft
flow. The [template guide](templates.md) shows the complete code and how to replace a worker.
Native Temporal workers and `GatewayActivities.call` remain available for advanced agent
registrations. Cancel through `agentworkflows runs cancel RUN_ID`; cancellation cannot
undo a tool action already sent.

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
