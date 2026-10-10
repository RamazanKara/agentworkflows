# CLI and Python SDK reference

This reference describes the Python SDK and CLI in release v0.9.0. Install the release wheel
(see [distribution](distribution.md)), or from a v0.9.0 checkout run
`python -m pip install ./sdk/python` in a virtual environment. Use a gateway of the same version.
`agentworkflows --help` and each subcommand's `--help` work offline, before you set a key.
The equivalent module entry point is `python -m agentworkflows.cli`.

The [team lifecycle reference](team-lifecycle.md) lists the Python and TypeScript methods
for versioned template installation, secret rotation/resolution, team retention,
data export/erasure and telemetry status, including access rules and version headers.

## CLI

| Command | Purpose |
| --- | --- |
| `init [DIRECTORY] --template research` | Scaffold a new or empty directory; defaults to current directory and research |
| `init DIRECTORY --template support-triage` | Scaffold ticket classification and a suggested reply |
| `init DIRECTORY --template code-review` | Scaffold diff review with human approval |
| `init DIRECTORY --template weekly-report` | Combine changes, support and incident sources into a weekly report |
| `init DIRECTORY --template incident-summary` | Summarize an incident log export with evidence references |
| `init DIRECTORY --template document-qa` | Answer from retrieved excerpts with checked citation IDs |
| `models` | Discover model IDs available to your credential |
| `chat "PROMPT" --model MODEL` | Governed model call; omit model for the gateway default |
| `team` | Your team, role, projects, and configured providers |
| `keys list` | List your team's managed API keys (admin only) |
| `keys create --name NAME --role builder --project PROJECT` | Issue a key; role defaults to viewer, project is optional; plaintext is printed once |
| `keys update KEY_ID --name NAME --role viewer --expires-at TIMESTAMP` | Change selected metadata; unspecified fields stay unchanged |
| `keys revoke KEY_ID` | Revoke immediately on all replicas, including sessions using the key |
| `settings show` | Effective team settings, policy defaults and revision (unrestricted admin only) |
| `settings set --fields '{"cost_limit_usd":100}' --revision 0` | Atomically override supplied fields; `--fields @settings.json` also works |
| `settings reset FIELD --revision 1` | Remove one override and restore its policy default |
| `audit list --limit 50 --cursor CURSOR` | One page of retained team receipts, newest first; cursor is optional |
| `audit verify --from UNIX_SECONDS --to UNIX_SECONDS` | Verify every team event in the selected time range; both bounds are optional |
| `audit export --output audit-log.jsonl` | Follow all cursor pages and write original events as JSON Lines; omit output or use `-` for stdout |
| `usage` | Usage, estimated spend, provider and workflow breakdowns |
| `usage --output usage.csv` | Export current UTC month usage as CSV; `--output -` writes CSV to stdout |
| `runs start [WORKFLOW] --input '@input.json'` | Start a workflow (default ResearchWorkflow); input can also be inline JSON |
| `runs list --project PROJECT --cursor CURSOR` | One run page; options are optional; pass next_cursor with the same filters for older runs |
| `runs export --status completed --output runs.jsonl` | Export full retained run details as JSON Lines; status/output are optional |
| `runs inspect RUN_ID` | Status, draft, completed result, budget, and timeline including opt-in step content |
| `runs approve RUN_ID` | Approve as your authenticated identity; `--reject` rejects instead |
| `runs cancel RUN_ID` | Request cancellation; the run stops from its current step onward |
| `runs retry RUN_ID` | Start a new execution after failure/cancellation; steps run again in the new execution |
| `triggers list` | Inspect configured schedules, webhook endpoints and pause state |
| `triggers pause WORKFLOW NAME` / `triggers resume WORKFLOW NAME` | Pause or resume a configured trigger |

`runs start` also accepts `--project` and `--request-id UUID`. If a start response is lost,
reuse the request ID printed on stderr with **identical input** to avoid duplicate runs.
Exit codes: **0** success, **1** gateway/transport failure, **2** usage/input/scaffold error.

Run cursors and JSON Lines export require gateway v0.9.0 or newer. `runs list` and `runs export` accept `--project`,
`--workflow`, `--status`, `--cursor` and `--limit` (1–100, default 20). Limit bounds
records scanned before filtering; an empty page can still have `next_cursor`.
Continue until it is null. Cursors use creation time and run ID, so subsequent pages
stay stable as runs are added or expire, including tied timestamps. Keep
project and filters unchanged while paging (a changed filter returns 422 `run_cursor_invalid`).
Legacy `--offset`/`next_offset` remain available; use either offsets or cursors in one listing.

Exports follow all pages, fetch each run's result, budget and timeline, and include
step input/output while retained and allowed by capture policy. The stored start input
stays on the server. Files are UTF-8; omit `--output` or use `-` for stdout. A read or
write failure exits 1; rerun the export for a complete file. Retention and run status changes
continue during export, so the file reflects retained runs at export time. Each call enforces
the credential's team and project access. Store exports according to your content policy;
downloaded files follow your own retention.

The Python methods are `runs(**filters) -> RunPage` and
`export_runs(**filters) -> Iterator[str]`, with `RunFilters`, `RunStatus` and
`RunPage` in `agentworkflows.types`. Each exported string is one full run detail
object followed by a newline. `GatewayError`/transport errors propagate on failed
reads, including when a run expires between its listing and detail request. Export
checks for cursor support first and prints an upgrade message for older gateways before writing rows.

Key creation and updates accept `--expires-at` as ISO-8601 with a timezone, for example
`2027-01-01T00:00:00Z`. On updates, `--expires-at ''` clears expiry and `--project ''`
clears a project binding. Commands return JSON; keep the `key_id` for future changes and
save a newly created `key` securely, since it is shown only once. Keys are scoped
to the authenticated admin's team and project access. Your current key is protected from
revocation and demotion by itself. Bootstrap file records continue to be edited in gateway configuration.
Machine-readable command output stays on stdout; actionable errors go to stderr.

Settings and audit SDK methods and CLI commands require an
unrestricted team admin credential. Run `settings show` before changing settings,
review the values, and pass its `revision` to `set` or `reset`. `--fields` is a
non-empty JSON object of field names to values, given directly as the top-level object.
Only supplied fields change. A 409 means another writer changed the settings:
reload, review and reapply with the new revision. You choose when to resubmit with a
fresh revision. A 422 reports invalid fields, and the patch is applied atomically. See [team settings](team-settings.md) for field names
and accepted values.

`audit list` and `audit export` accept `--from`, `--to` (inclusive Unix seconds),
`--event-type`, `--actor`, `--project`, `--run-id`, `--cursor`, and `--limit` (1–200,
default 50 per page). Pass the list result's `next_cursor` to read older events.
Export follows cursors until exhausted and captures the current time as `--to`
when omitted, keeping it fixed throughout the export. File output is UTF-8.
Verification accepts only time bounds and checks all team events in that range,
including events hidden by other filters. `audit verify` prints the full result
and exits **1** for a broken chain or disabled view; `audit export` also exits **1**
when disabled. A disabled `audit list` returns its `enabled: false` JSON response.
Failed exports exit nonzero; rerun them for a complete file. Retention continues
during paging. For full process logs, use the operator verifier. See [audit log](audit-log.md)
for boundaries and verification.

The [template gallery](templates.md) includes input fields, expected results and adaptation
steps for every starter. The release wheel and a v0.9.0 checkout carry the same template set.
Scaffolds include `input-schema.json` and a schema declaration in `workflow.py`. Copy the
schema to the workflow policy's `inputSchema` to enable console forms and gateway validation.

## Company group access

Gateway v0.9.0 provides `GET /v1/team/sso` for a team admin credential without a project
restriction. It returns `TeamSSO`: team ID, enabled state, provider hostname,
`role_source` (`groups` or `claim`), team/project claim names, and only the current
team's group-to-role mappings. In group mode, `role_claim` and `default_role` are
null; in claim mode, `groups_claim` is null and mappings are empty. The response covers
the current team's policy only, with secrets and user group memberships kept private.
A disabled provider has `enabled: false` and `provider_name: null`.

```python
from agentworkflows import GatewayClient

with GatewayClient("https://agents.example.com", api_key=admin_key) as client:
    policy = client.team_sso()
    print(policy["role_source"], policy["group_role_mappings"])
```

```typescript
import { GatewayClient } from '@agentworkflows/sdk';

const client = new GatewayClient('https://agents.example.com', { apiKey: adminKey });
const policy = await client.teamSSO();
console.log(policy.role_source, policy.group_role_mappings);
```

Both helpers use the SDK's authenticated GET retries and preserve 401/403 errors.
This is an inspection API; an operator configures mappings through Helm or
environment variables. See [group access semantics and acceptance](workflows.md#sso-group-to-role-mapping).
Members & keys displays the same policy. Group membership is managed in your identity provider.

## Trigger history and usage CSV

`runs list` and `runs export` accept `--trigger NAME` together with `--workflow WORKFLOW`.
The API uses `GET /v1/workflow-runs?workflow=WORKFLOW&trigger=NAME`; retain both filters
when following `next_cursor`. Missing workflow returns 422 `trigger_workflow_required`.
Run metadata includes `trigger: {name, kind}` for cron/webhook launches.
Manual runs and retries are listed under the workflow alone. History survives
removal of the trigger's configuration, until run retention expires. Rejected deliveries
are recorded in their audit receipts.

```python
from agentworkflows import GatewayClient

with GatewayClient("http://127.0.0.1:8080", api_key="local-development-only") as gateway:
    page = gateway.runs(workflow="DailyReportWorkflow", trigger="daily")
    with open("usage.csv", "w", encoding="utf-8", newline="") as output:
        output.write(gateway.export_usage())
```

```typescript
import { writeFile } from 'node:fs/promises';
import { GatewayClient } from '@agentworkflows/sdk';
const gateway = new GatewayClient('http://127.0.0.1:8080', { apiKey: 'local-development-only' });
const page = await gateway.runs({ workflow: 'DailyReportWorkflow', trigger: 'daily' });
await writeFile('usage.csv', await gateway.exportUsage(), 'utf8');
```

`GET /v1/usage/export` returns UTF-8 `text/csv` with an attachment filename and
`Cache-Control: no-store`. All team roles may export. A project-bound credential sees
only that project; an unrestricted credential sees team totals. Columns are
`team_id,project,period_start,period_end,dimension,name,calls,tokens,cost_usd,currency`.
The period is the current UTC month (`period_end` exclusive); currency is USD and costs
have nine decimal places. There is one `total` row, then sorted `provider` and `workflow`
rows. Empty months have a zero total. Provider and workflow rows are overlapping views of the
same total, so sum one dimension at a time.
Totals include tool costs and held reservations; call/token counts come from provider
accounting. Data uses configured prices. The export covers the current month at the
provider/workflow level. CSV names that could execute spreadsheet formulas
have a leading apostrophe; quotes, commas, newlines and Unicode are escaped by CSV rules.
Export errors propagate through each SDK's existing error and read-retry handling.

## Environment

| Variable | Default / use |
| --- | --- |
| `AGENTWORKFLOWS_API_KEY` | Required gateway credential (provider keys stay on the gateway); `local-development-only` for local human use, `demo-worker` for its worker |
| `AGENTWORKFLOWS_URL` | `http://127.0.0.1:8080` for CLI and worker |
| `AGENTWORKFLOWS_TEAM` | `demo`; worker queue defaults to `TEAM-workflows` |
| `TEMPORAL_ADDRESS` | `localhost:7233` for the worker |
| `TEMPORAL_NAMESPACE` | `default` for the worker |
| `TEMPORAL_TASK_QUEUE` | Optional existing override; must match the gateway's team queue |

## Workflow SDK

Import `WorkflowGateway`, `Budget`, and `ApprovalWorkflow` from `agentworkflows.workflows`.
Use `@input_schema(schema)` from the same module on a workflow class to declare its inputs;
the schema is available as `WorkflowClass.input_schema`. In TypeScript, import `InputSchema`
and `withInputSchema` from `@agentworkflows/sdk/workflows`, then export
`withInputSchema(schema, implementation)`; it preserves the function and exposes `.inputSchema`.
Administrator-approved workflow policies stay authoritative; copy the declaration into policy to apply it.

Schemas use the flat JSON Schema draft 2020-12 subset documented in
[workflow forms](workflows.md#workflow-forms-and-step-content): object properties with primitive
types or string arrays, enums, required fields, descriptions, defaults, and examples.

| API | Result / behavior |
| --- | --- |
| `WorkflowGateway(Budget(10000, 5.0))` | Per-run token and USD ceilings, further restricted by team policy; these are the defaults |
| `await gateway.text(prompt, model=..., max_tokens=512)` | Text answer; omit model for the gateway default |
| `await gateway.model(messages, model=..., max_tokens=512)` | Full chat-completion dictionary when you need usage or structured message fields |
| `await gateway.tool(name, arguments)` | Approved HTTP/MCP tool's result |
| `await gateway.agent(name, arguments)` | Registered framework handler's result through governed adapters |
| `await gateway.container(name, arguments)` | Approved sandbox's output; runs once per call, with retries left to your workflow |
| `await self.approval(draft)` | In an ApprovalWorkflow subclass: await one human decision per run; returns bool, sets reviewer |
| `run_worker([WorkflowClass])` | Import from `agentworkflows.worker`; start an environment-configured worker with concurrency capped at two |

Activity defaults are three minutes per attempt, fifteen minutes overall, and five attempts
with exponential backoff. Policy denials are final; transient gateway failures retry. Existing Temporal `RetryPolicy` and `timedelta` parameters remain available on
`WorkflowGateway`. `data_classification` defaults to `internal`; confidential data needs an
approved self-hosted route. Approval expires after seven days with an actionable failure.

Both SDKs send model/tool input and output through gateway capture automatically when
the policy enables `captureContent`. Run inspection includes per-step text, redaction mode,
and per-field truncation flags. Capture uses a separate Redis TTL (seven days by default),
stored apart from receipt bodies. Terminal run records default to 30-day retention.

Template research budgets accept integer `token_limit` from 1 to 1,000,000,000 and finite
`cost_limit_usd` greater than zero up to 1,000,000. Policy ceilings still take precedence.

## Gateway client and errors

Use `GatewayClient(base_url, api_key=...)` as a context manager for authenticated calls.
Its `start_run`, `runs`, `run`, `approve_run`, `cancel_run`, and `retry_run` methods mirror
the CLI. [Client examples](client-examples.md) cover model calls and compatible framework SDKs.

The admin methods return typed dictionaries, with request/response types available
from `agentworkflows.types` (`ManagedKey`, `CreatedKey`, `KeyOptions`, `KeyUpdate`,
`KeyList`, `Role`, `TeamSettings`, `TeamSetting`, `TeamSettingValue`, `AuditFilters`,
`AuditEntry`, `AuditPage`, `AuditPosition`, and `AuditVerification`). Responses retain
the API's field names and timestamps in Unix seconds; key **input** expiry uses an
ISO-8601 string with a timezone.

| Client method | Result / behavior |
| --- | --- |
| `list_keys()` | `KeyList`, including expired and revoked managed keys |
| `create_key(name, role="viewer", project=..., expires_at=...)` | `CreatedKey`; plaintext `key` appears only in this response; options are optional |
| `update_key(key_id, name=..., role=..., project=..., expires_at=...)` | `ManagedKey`; only supplied fields change; `None` clears project or expiry |
| `revoke_key(key_id)` | Revoked `ManagedKey`; the current key is protected from revoking itself |
| `team_settings()` | `TeamSettings`: revision, update metadata, fields with value/source/policy_default, and available routes/providers/approver_roles |
| `update_team_settings(fields, revision=...)` | Atomically apply the field/value map and return `TeamSettings`; revision or quoted ETag is sent as `If-Match` |
| `reset_team_setting(field, revision=...)` | Reset one field to policy and return `TeamSettings` |
| `audit(**filters)` | One `AuditPage` with envelopes, original events and `next_cursor` |
| `verify_audit(from_time=..., to=...)` | `AuditVerification` with `ok`, `checked`, `first_break` and `boundaries`; `ok` is `None` when disabled |
| `export_audit(**filters)` | Iterator of newline-terminated JSON strings, one original event per line across all pages; disabled views raise `RuntimeError` |

Audit filter names are `from_time`, `to`, `event_type`, `actor`, `project`, `run_id`,
`cursor`, and `limit`. Both time bounds are inclusive Unix seconds. All filters
are optional; `limit` defaults to 50 on the gateway, with a maximum of 200.

```python
from agentworkflows import GatewayClient

with GatewayClient("http://127.0.0.1:8080", api_key="YOUR_ADMIN_KEY") as gateway:
    settings = gateway.team_settings()
    saved = gateway.update_team_settings({"cost_limit_usd": 100}, revision=settings["revision"])
    gateway.reset_team_setting("cost_limit_usd", revision=saved["revision"])
    with open("audit-log.jsonl", "w", encoding="utf-8", newline="\n") as output:
        output.writelines(gateway.export_audit(event_type="team_settings_changed"))
```

Admin mutations surface ambiguous HTTP failures and read timeouts to the caller for review. Settings
conflicts and validation failures surface as `GatewayError` with the original
status and structured detail; settings field errors are in `detail["fields"]`.

`GatewayError` exposes `status_code`, `reason`, `request_id`, and `detail`. Validation errors
identify the invalid fields. CLI output includes a recovery action and request ID, and keeps your credential private. `GatewayRetryAfterError.retry_after` reports when the server asks
for a delay longer than the client's retry cap; transport errors are `httpx.HTTPError`.
Schema errors return HTTP 422 with `detail.reason = "workflow_input_invalid"` and a
`detail.fields` list of `{field: "input.property", message: "..."}`. Validation happens
before a start intent or Temporal execution is created, and the API validates input exactly as sent.

| Error | Recovery |
| --- | --- |
| Authentication / wrong role | Set a valid team gateway key; ask the admin for builder or approver access as appropriate |
| `workflow_input_invalid` | Use the template's input.json and correct the named field before starting again |
| `workflow_not_allowed`, `model_not_allowed`, `tool_not_allowed` | Discover approved models with `models`; review team workflow policy with the admin |
| `prompt_secret_detected` | Remove credentials or sensitive personal data from the prompt/tool arguments |
| `approval_not_waiting` | Inspect the run's stage and existing decision; read each draft before approving |
| `workflow_start_conflict` | Use the original input for that request ID, or a new ID for a different run |
| `team_settings_conflict` (409) | Reload with `settings show`, review and retry with its revision |
| `team_settings_invalid` (422) | Correct the named fields using `settings show` and the team settings reference |
| `audit_range_invalid` (422) | Use inclusive Unix timestamps with `from` at or before `to` |
| `audit_view_unavailable` (503) | Restore the audit Redis backend, then rerun the command for a complete export |
| `workflow_run_missing` | Use `runs list` with the correct team and project |
| `temporal_unavailable`, `worker_unavailable` | Check Temporal health and the team's worker/queue, then inspect again |
| Budget / rate limit | Inspect `usage`; wait for the window or ask an admin to review the limit |

The [authenticated API table](workflows.md#authenticated-workflow-api) documents the run
endpoints. The full [OpenAPI contract](https://github.com/RamazanKara/agentworkflows/blob/main/platform/api-contracts/inference-gateway.openapi.json)
is checked in and can be viewed at `/docs` on the running gateway when enabled.


## Approval quorum and expiry

Upgrade the gateway before workers. Both SDKs' `ApprovalWorkflow.approval(draft)`
read the run's saved policy through a versioned worker activity. Existing approval
gates pick this up with their current workflow code. `review` accepts a vote, while
`approval` resolves at quorum or rejection; expiry raises `ApprovalExpired`.
The TypeScript `ApprovalProgress` type includes the quorum, reviewer IDs and deadline.
Python exposes the same fields in the status query and `GatewayClient.run()` response.

Use the existing settings helpers to change the policy for new runs:

```python
settings = client.team_settings()
client.update_team_settings({
    "workflows.ResearchWorkflow.required_approvals": 2,
    "workflows.ResearchWorkflow.approval_timeout_seconds": 3600,
}, revision=settings["revision"])
```

```typescript
const settings = await client.teamSettings();
await client.updateTeamSettings({
  'workflows.ResearchWorkflow.required_approvals': 2,
  'workflows.ResearchWorkflow.approval_timeout_seconds': 3600,
}, { revision: settings.revision });
```

`approve_run` / `approveRun` retain their existing signature. A successful positive
vote can leave the run waiting for other reviewers; inspect the run to see its current state.
Each reviewer counts once, no matter how many times they vote. See [approval policies](workflows.md#approval-policies)
for identity, replay, automatic decisions and upgrade boundaries.

## Team adoption APIs

| Task | Python | TypeScript | HTTP |
| --- | --- | --- | --- |
| First-run readiness | `onboarding()` | `onboarding()` | `GET /v1/team/onboarding` |
| Save provider key | `set_provider_key(provider, value, expected_version=0)` | `setProviderKey(provider, value, 0)` | `PUT /v1/team/providers/{provider}/key` |
| Invitations | `invitations()`, `create_invitation(name, role="viewer")`, `revoke_invitation(id)` | `invitations()`, `createInvitation(name, {role: "viewer"})`, `revokeInvitation(id)` | `GET/POST /v1/team/invitations`, `DELETE /v1/team/invitations/{id}` |
| Redeem invitation once | `accept_invitation(token)` | `acceptInvitation(token)` | `POST /v1/auth/invitations/accept` |
| Alert rules | `alert_rules()`, `set_alert_rules(rules, revision=revision)` | `alertRules()`, `setAlertRules(rules, revision)` | `GET/PUT /v1/team/alert-rules` |
| Prove an install | `first_approved_run(client, approvers=[...], deadline=300, allow_paid=False)` (`agentworkflows.acceptance`), CLI `agentworkflows check` | `firstApprovedRun(client, { approvers, deadlineSeconds, allowPaid })` | the console's first-run path: onboarding, template install, run, approval, audit verify |
| Run insights | `workflow_insights(days=7, project=None, workflow=None)` | `workflowInsights({ days, project, workflow })` | `GET /v1/workflow-insights` |
| Register your own workflow | `team_workflows()`, `register_workflow(cls_or_name, models=[...], **options)`, `remove_workflow(cls_or_name)` | `teamWorkflows()`, `registerWorkflow(name, models, options?, revision?)`, `removeWorkflow(name, revision?)` | `GET /v1/team/workflows`, `PUT/DELETE /v1/team/workflows/{name}` |
| Deployment settings | `deployment()` | `deployment()` | `GET /v1/team/deployment` |

Invitation creation and redemption run once per call, since the response holds the only
copy of a token or key. If a response is lost, ask an admin to revoke that access and
issue a fresh invitation. See [invitations and alerts](workflows.md),
[provider onboarding](quickstart.md#60-second-console-walkthrough), and
[deployment checks](single-tenant.md#install-and-verify) for roles, boundaries and examples.
