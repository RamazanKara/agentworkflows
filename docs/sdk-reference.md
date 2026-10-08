# CLI and Python SDK reference

Install the released SDK in a virtual environment with
`python -m pip install https://github.com/RamazanKara/agentworkflows/releases/download/v0.5.1/agentworkflows-0.5.1-py3-none-any.whl`
(or `python -m pip install ./sdk/python` from a checkout). `agentworkflows --help` and each subcommand's `--help` work without a key.
The equivalent module entry point is `python -m agentworkflows.cli`.

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
| `runs start [WORKFLOW] --input '@input.json'` | Start a workflow (default ResearchWorkflow); input can also be inline JSON |
| `runs list --project PROJECT --offset OFFSET` | List runs; both options are optional; use returned next_offset for pagination |
| `runs inspect RUN_ID` | Status, draft, completed result, budget, and timeline including opt-in step content |
| `runs approve RUN_ID` | Approve as your authenticated identity; `--reject` rejects instead |
| `runs cancel RUN_ID` | Request cancellation; cannot undo already-sent tool actions |
| `runs retry RUN_ID` | Start a new execution after failure/cancellation; steps may run again |
| `triggers list` | Inspect configured schedules, webhook endpoints and pause state |
| `triggers pause WORKFLOW NAME` / `triggers resume WORKFLOW NAME` | Pause or resume a configured trigger |

`runs start` also accepts `--project` and `--request-id UUID`. If a start response is lost,
reuse the request ID printed on stderr with **identical input** to avoid duplicate runs.
Exit codes: **0** success, **1** gateway/transport failure, **2** usage/input/scaffold error.

Key creation and updates accept `--expires-at` as ISO-8601 with a timezone, for example
`2027-01-01T00:00:00Z`. On updates, `--expires-at ''` clears expiry and `--project ''`
clears a project binding. Commands return JSON; keep the `key_id` for future changes and
save a newly created `key` securely, since list/update cannot recover it. Keys are scoped
to the authenticated admin's team and project access. You cannot revoke or demote your
current key. Bootstrap file records continue to be edited in gateway configuration.
Machine-readable command output stays on stdout; actionable errors go to stderr.

Settings and audit SDK methods and CLI commands are available from this checkout
(v0.6.0 in progress); they are not in the v0.5.1 wheel above. Both require an
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
