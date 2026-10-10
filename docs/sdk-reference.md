# CLI and Python SDK reference

This reference describes the current source SDK (0.9.0) under review for 1.0.0-rc.2.
Install it in a virtual environment from the repository root with
`python -m pip install ./sdk/python`; run a gateway built from the same checkout.
The older v0.5.1 release wheel does not implement the entire surface below.
`agentworkflows --help` and each subcommand's `--help` work without a key.
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
| `runs cancel RUN_ID` | Request cancellation; cannot undo already-sent tool actions |
| `runs retry RUN_ID` | Start a new execution after failure/cancellation; steps may run again |
| `triggers list` | Inspect configured schedules, webhook endpoints and pause state |
| `triggers pause WORKFLOW NAME` / `triggers resume WORKFLOW NAME` | Pause or resume a configured trigger |

`runs start` also accepts `--project` and `--request-id UUID`. If a start response is lost,
reuse the request ID printed on stderr with **identical input** to avoid duplicate runs.
Exit codes: **0** success, **1** gateway/transport failure, **2** usage/input/scaffold error.

Run cursors and JSON Lines export require gateway v0.6.0 or newer; use the v0.9.0 checkout SDK and gateway,
not the v0.5.1 wheel or images. `runs list` and `runs export` accept `--project`,
`--workflow`, `--status`, `--cursor` and `--limit` (1–100, default 20). Limit bounds
records scanned before filtering; an empty page can still have `next_cursor`.
Continue until it is null. Cursors use creation time and run ID, so new runs and
expired entries do not shift subsequent pages, including tied timestamps. Keep
project and filters unchanged; otherwise the API returns 422 `run_cursor_invalid`.
Legacy `--offset`/`next_offset` remain available; don't combine them with cursors.

Exports follow all pages, fetch each run's result, budget and timeline, and include
step input/output only while retained and allowed by capture policy. They omit the
stored start input. Files are UTF-8; omit `--output` or use `-` for stdout. A read or
write failure exits 1 and can leave a partial file. Retention and run status changes
continue during export: this is not a transactional snapshot or backup, and it does
not recover expired runs or content. Each call enforces the credential's team and
project access. Store exports according to your content policy; server expiry does
not remove downloaded files.

The Python methods are `runs(**filters) -> RunPage` and
`export_runs(**filters) -> Iterator[str]`, with `RunFilters`, `RunStatus` and
`RunPage` in `agentworkflows.types`. Each exported string is one full run detail
object followed by a newline. `GatewayError`/transport errors propagate on failed
reads, including when a run expires between its listing and detail request. Export
rejects gateways without cursor support with an upgrade message before writing rows.

Key creation and updates accept `--expires-at` as ISO-8601 with a timezone, for example
`2027-01-01T00:00:00Z`. On updates, `--expires-at ''` clears expiry and `--project ''`
clears a project binding. Commands return JSON; keep the `key_id` for future changes and
save a newly created `key` securely, since list/update cannot recover it. Keys are scoped
to the authenticated admin's team and project access. You cannot revoke or demote your
current key. Bootstrap file records continue to be edited in gateway configuration.
Machine-readable command output stays on stdout; actionable errors go to stderr.

Settings and audit SDK methods and CLI commands are available from this checkout;
they are not in the v0.5.1 wheel. Both require an
unrestricted team admin credential. Run `settings show` before changing settings,
review the values, and pass its `revision` to `set` or `reset`. `--fields` is a
non-empty JSON object of field names to values, without an outer `fields` wrapper.
Only supplied fields change. A 409 means another writer changed the settings:
reload, review and reapply with the new revision. The CLI never refreshes the
revision or resubmits a stale change automatically. A 422 reports invalid fields;
the entire patch is rejected. See [team settings](team-settings.md) for field names
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
Failed exports exit nonzero and may leave partial output. Retention continues
during paging; these exports are not complete process logs for the operator
verifier. See [audit log](audit-log.md) for boundaries and verification limits.

The [template gallery](templates.md) includes input fields, expected results and adaptation
steps for every starter. Install from this checkout to get its current template set.
Scaffolds include `input-schema.json` and a schema declaration in `workflow.py`. Copy the
schema to the workflow policy's `inputSchema` to enable console forms and gateway validation.

## Company group access (v0.8.0)

Gateway v0.8.0 adds `GET /v1/team/sso` for a team admin credential without a project
restriction. It returns `TeamSSO`: team ID, enabled state, provider hostname,
`role_source` (`groups` or `claim`), team/project claim names, and only the current
team's group-to-role mappings. In group mode, `role_claim` and `default_role` are
null; in claim mode, `groups_claim` is null and mappings are empty. No secrets,
other teams' mappings or user group memberships are returned. A disabled provider
has `enabled: false` and `provider_name: null`.

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
Members & keys displays the same policy. The API does not log users into their
identity provider, create groups or change memberships.

## Trigger history and usage CSV (v0.7.0 candidate)

`runs list` and `runs export` accept `--trigger NAME` together with `--workflow WORKFLOW`.
The API uses `GET /v1/workflow-runs?workflow=WORKFLOW&trigger=NAME`; retain both filters
when following `next_cursor`. Missing workflow returns 422 `trigger_workflow_required`.
Run metadata includes `trigger: {name, kind}` for cron/webhook launches recorded by
v0.7.0. Older/manual runs and manual retries have no trigger provenance. History survives
removal of the trigger's configuration, until run retention expires. Rejected deliveries
do not have a run; inspect their audit receipts instead.

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
rows. Empty months have a zero total. These are overlapping views, **not additive rows**.
Totals include tool costs and held reservations; call/token counts come from provider
accounting. Data uses configured prices, not provider invoices. Older months and raw
per-call data are outside this export. CSV names that could execute spreadsheet formulas
have a leading apostrophe; quotes, commas, newlines and Unicode are escaped by CSV rules.
Export errors propagate through each SDK's existing error and read-retry handling.

## Environment

| Variable | Default / use |
| --- | --- |
| `AGENTWORKFLOWS_API_KEY` | Required gateway credential, never a provider key; `local-development-only` for local human use, `demo-worker` for its worker |
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
Declarations do not register or replace administrator-approved workflow policies.

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
| `await gateway.container(name, arguments)` | Approved sandbox's output; no automatic retry of arbitrary code |
| `await self.approval(draft)` | In an ApprovalWorkflow subclass: await one human decision per run; returns bool, sets reviewer |
| `run_worker([WorkflowClass])` | Import from `agentworkflows.worker`; start an environment-configured worker with concurrency capped at two |

Activity defaults are three minutes per attempt, fifteen minutes overall, and five attempts
with exponential backoff. Policy denials are non-retryable; transient gateway failures can
retry. Existing Temporal `RetryPolicy` and `timedelta` parameters remain available on
`WorkflowGateway`. `data_classification` defaults to `internal`; confidential data needs an
approved self-hosted route. Approval expires after seven days with an actionable failure.

Both SDKs send model/tool input and output through gateway capture automatically when
the policy enables `captureContent`. Run inspection includes per-step text, redaction mode,
and per-field truncation flags. Capture uses a separate Redis TTL (seven days by default),
never receipt bodies. Terminal run records default to 30-day retention.

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
| `revoke_key(key_id)` | Revoked `ManagedKey`; the current key cannot revoke itself |
| `team_settings()` | `TeamSettings`: revision, update metadata, fields with value/source/policy_default, and available routes/providers/approver_roles |
| `update_team_settings(fields, revision=...)` | Atomically apply the field/value map and return `TeamSettings`; revision or quoted ETag is sent as `If-Match` |
| `reset_team_setting(field, revision=...)` | Reset one field to policy and return `TeamSettings` |
| `audit(**filters)` | One `AuditPage` with envelopes, original events and `next_cursor` |
| `verify_audit(from_time=..., to=...)` | `AuditVerification` with `ok`, `checked`, `first_break` and `boundaries`; `ok` is `None` when disabled |
| `export_audit(**filters)` | Iterator of newline-terminated JSON strings, one original event per line across all pages; disabled views raise `RuntimeError` |

Audit filter names are `from_time`, `to`, `event_type`, `actor`, `project`, `run_id`,
`cursor`, and `limit`. Both time bounds are inclusive Unix seconds. All filters
are optional; `limit` defaults to 50 on the gateway and cannot exceed 200.

```python
from agentworkflows import GatewayClient

with GatewayClient("http://127.0.0.1:8080", api_key="YOUR_ADMIN_KEY") as gateway:
    settings = gateway.team_settings()
    saved = gateway.update_team_settings({"cost_limit_usd": 100}, revision=settings["revision"])
    gateway.reset_team_setting("cost_limit_usd", revision=saved["revision"])
    with open("audit-log.jsonl", "w", encoding="utf-8", newline="\n") as output:
        output.writelines(gateway.export_audit(event_type="team_settings_changed"))
```

Admin mutations do not retry ambiguous HTTP failures or read timeouts. Settings
conflicts and validation failures surface as `GatewayError` with the original
status and structured detail; settings field errors are in `detail["fields"]`.

`GatewayError` exposes `status_code`, `reason`, `request_id`, and `detail`. Validation errors
identify the invalid fields. CLI output includes a recovery action and request ID without
printing your credential. `GatewayRetryAfterError.retry_after` reports when the server asks
for a delay longer than the client's retry cap; transport errors are `httpx.HTTPError`.
Schema errors return HTTP 422 with `detail.reason = "workflow_input_invalid"` and a
`detail.fields` list of `{field: "input.property", message: "..."}`. Validation happens
before a start intent or Temporal execution is created; defaults are not inserted by the API.

| Error | Recovery |
| --- | --- |
| Authentication / wrong role | Set a valid team gateway key; ask the admin for builder or approver access as appropriate |
| `workflow_input_invalid` | Use the template's input.json and correct the named field before starting again |
| `workflow_not_allowed`, `model_not_allowed`, `tool_not_allowed` | Discover approved models with `models`; review team workflow policy with the admin |
| `prompt_secret_detected` | Remove credentials or sensitive personal data from the prompt/tool arguments |
| `approval_not_waiting` | Inspect the run's stage and existing decision; never approve an unseen draft |
| `workflow_start_conflict` | Use the original input for that request ID, or a new ID for a different run |
| `team_settings_conflict` (409) | Reload with `settings show`, review and retry with its revision |
| `team_settings_invalid` (422) | Correct the named fields using `settings show` and the team settings reference |
| `audit_range_invalid` (422) | Use inclusive Unix timestamps with `from` at or before `to` |
| `audit_view_unavailable` (503) | Restore the audit Redis backend before retrying; an export may be partial |
| `workflow_run_missing` | Use `runs list` with the correct team and project |
| `temporal_unavailable`, `worker_unavailable` | Check Temporal health and the team's worker/queue, then inspect again |
| Budget / rate limit | Inspect `usage`; wait for the window or ask an admin to review the limit |

The [authenticated API table](workflows.md#authenticated-workflow-api) documents the run
endpoints. The full [OpenAPI contract](https://github.com/RamazanKara/agentworkflows/blob/main/platform/api-contracts/inference-gateway.openapi.json)
is checked in and can be viewed at `/docs` on the running gateway when enabled.


## Approval quorum and expiry (v0.9.0)

Upgrade the gateway before workers. Both SDKs' `ApprovalWorkflow.approval(draft)`
read the run's saved policy through a versioned worker activity. No workflow-code
change is needed for an existing approval gate. `review` accepts a vote, while
`approval` resolves only after quorum or rejection; expiry raises `ApprovalExpired`.
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
vote can leave the run waiting for other reviewers; inspect the run before assuming
it completed. Repeating a vote never adds a reviewer. See [approval policies](workflows.md#approval-policies)
for identity, replay, automatic decisions and upgrade boundaries.

## Team adoption APIs (rc.4 candidate)

| Task | Python | TypeScript | HTTP |
| --- | --- | --- | --- |
| First-run readiness | `onboarding()` | `onboarding()` | `GET /v1/team/onboarding` |
| Save provider key | `set_provider_key(provider, value, expected_version=0)` | `setProviderKey(provider, value, 0)` | `PUT /v1/team/providers/{provider}/key` |
| Invitations | `invitations()`, `create_invitation(name, role="viewer")`, `revoke_invitation(id)` | `invitations()`, `createInvitation(name, {role: "viewer"})`, `revokeInvitation(id)` | `GET/POST /v1/team/invitations`, `DELETE /v1/team/invitations/{id}` |
| Redeem invitation once | `accept_invitation(token)` | `acceptInvitation(token)` | `POST /v1/auth/invitations/accept` |
| Alert rules | `alert_rules()`, `set_alert_rules(rules, revision=revision)` | `alertRules()`, `setAlertRules(rules, revision)` | `GET/PUT /v1/team/alert-rules` |
| Run insights | `workflow_insights(days=7, project=None, workflow=None)` | `workflowInsights({ days, project, workflow })` | `GET /v1/workflow-insights` |
| Register your own workflow | `team_workflows()`, `register_workflow(cls_or_name, models=[...], **options)`, `remove_workflow(cls_or_name)` | `teamWorkflows()`, `registerWorkflow(name, models, options?, revision?)`, `removeWorkflow(name, revision?)` | `GET /v1/team/workflows`, `PUT/DELETE /v1/team/workflows/{name}` |
| Deployment settings | `deployment()` | `deployment()` | `GET /v1/team/deployment` |

Invitation creation and redemption never automatically retry: a lost response can
contain the only copy of a token or key. Ask an admin to revoke uncertain access and
issue a fresh invitation. See [invitations and alerts](workflows.md),
[provider onboarding](quickstart.md#60-second-console-walkthrough), and
[deployment checks](single-tenant.md#install-and-verify) for roles, boundaries and examples.
