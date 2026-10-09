# Team settings

Team admins can change budgets, approval rules, allowed providers and model alias
selections in **Team settings** in the console. **Providers & budgets** and **Costs**
include the budget editor. Other roles see effective values without editing
controls. Provider API keys stay in Kubernetes Secrets or gateway environment
variables; this API does not accept credentials, connection URLs or new routes.

Model choices use the same route IDs and provider names shown in **Get started**.
For admins, **Ready** means the provider key is configured (or the route is
simulated), and **Key missing** means it still needs a key. This is a configuration
check, not a live provider test. Costs and Providers name the configured token
window separately from the UTC calendar-month spending budget.

## Defaults and overrides

`SandboxPolicySet` remains the bootstrap and policy default. Overrides live in one
JSON document per team in the existing budget Redis, at
`<SANDBOX_BUDGET_KEY_PREFIX>:team-settings:<team-id>`. The document contains
`revision`, `updated_by`, `updated_at` and `overrides`. Enable Redis persistence and
include these documents in backups. No new environment variables or Helm values
are required.

With `STORAGE_BACKEND=postgres`, the same documents and revision checks use
[PostgreSQL gateway storage](postgresql-storage.md). Include that database in backups.

Every request reads effective settings from the selected store over the loaded YAML defaults;
there is no process cache. Changes are visible across gateway replicas without
restarting. Storage outages fail closed with 503. YAML changes still require the
normal configuration rollout, and do not erase overrides. **Reset to policy
default** removes one override and uses the current loaded YAML default.

| API field | YAML default | Meaning |
| --- | --- | --- |
| `cost_limit_usd` | `budgets.costLimitUsd` | Team budget in USD per UTC calendar month |
| `project_budgets.<project>` | `budgets.projectCostLimitsUsd.<project>` | Project budget in the same month; project must already exist |
| `workflows.<name>.token_limit` | `workflows.<name>.tokenLimit` | Token ceiling for each run |
| `workflows.<name>.cost_limit_usd` | `workflows.<name>.costLimitUsd` | USD ceiling for each run |
| `workflows.<name>.approval_required` | `workflows.<name>.approvalRequired` (default `true`) | Require review at the workflow's approval gate |
| `workflows.<name>.approval_threshold_usd` | `workflows.<name>.approvalThresholdUsd` (default `0`) | Require review when run spend at the gate is at least this amount |
| `workflows.<name>.approver_role` | `workflows.<name>.approverRole` (default `approver`) | `approver` or `admin`; admins can always review |
| `workflows.<name>.allowed_providers` | `workflows.<name>.allowedProviders` | Provider names eligible for this workflow |
| `model_routes.<alias>` | Alias assignment in `model-routing.yaml` | Canonical ID of an existing route |

Dollar overrides must be finite numbers between 0 and 1,000,000; token overrides
must be integers between 0 and 1,000,000,000. Zero is a spending/token ceiling,
not unlimited. An absent team/project YAML limit means unlimited; reset to restore
that default. Booleans and strings are not accepted as numbers.

Team and project reservations are checked atomically against the same monthly
counter across providers and tools. Editing a limit does not clear spend. Token
and request counters retain their configured `SANDBOX_BUDGET_WINDOW_SECONDS`.
The effective run ceiling is the lower of the worker's immutable requested budget
and the current workflow setting. Lowering a setting can stop an active run's next
call; raising it cannot exceed the worker's requested budget. Runs initialized by
older gateways retain any lower cap already stored for that run.

Approval settings govern the existing SDK `ApprovalWorkflow` review gate. A run
below the threshold, or with approval disabled, receives an audited automatic
decision there. Spend includes conservative reservations for unreported calls.
These settings do not insert a review step into workflow code that has no gate.
Changing the threshold affects subsequent gate checks, not decisions already
made or reviews already waiting. The current approver role is checked when a human
submits a decision, including for waiting runs.

Routes, prices, credentials, model allowlists and egress stay in YAML. Selecting an
alias target cannot bypass those controls; select a route the workflow already
permits. Fallback, canary and shadow routes retain their normal admission checks.
If an operator removes an overridden target from YAML, that alias fails closed
until an admin selects a valid target or resets it.

## API and conflicts

These three endpoints require a team-bound, unrestricted **admin** credential.
Other roles receive 403 with `detail.reason: team_role_required`. Project-bound
admins cannot edit team-wide settings. Read-only consumers use `GET /v1/team` and
`GET /v1/workflow-policies`.

`GET /v1/team/settings` returns the revision, update metadata, available route IDs
and a `fields` map. Each entry has `value`, `source` (`policy` or `override`) and
`policy_default`. For example, a budget field can be
`"cost_limit_usd": {"value": 50, "source": "policy", "policy_default": 50}`.

Send only edited fields in a PATCH, using the revision from GET in `If-Match`:

```http
PATCH /v1/team/settings
If-Match: 0
Content-Type: application/json

{"fields":{"cost_limit_usd":100,"project_budgets.default":40,"workflows.ResearchWorkflow.approver_role":"admin","model_routes.research":"anthropic"}}
```

The names above are examples; use the field names and route IDs returned by your
gateway. Validation is atomic: 422 includes `detail.fields` entries with `field`
and `message`, and no part of the patch is saved. Malformed request bodies and
missing headers use the usual FastAPI validation locations. Unknown fields,
providers, projects, workflows, aliases and routes are rejected.

`DELETE /v1/team/settings/cost_limit_usd` with `If-Match: 1` resets just that field.
URL-encode the field name in reset requests. GET and successful writes also return
a quoted revision in `ETag`, which can be used as `If-Match`.

A stale revision returns 409 with `detail.reason: team_settings_conflict` for
PATCH and DELETE. Reload, review the current values, then reapply the intended
change. The console retains the unsaved draft until you choose **Reload settings**;
it never silently retries a stale write. Cookie-authenticated writes also require
the normal CSRF token.

Every successful save/reset emits a hash-chained `team_settings_changed` audit
event with actor, revision and each touched field's before/after value and source.
Rejected changes do not emit change events. Verify them with the existing
[audit-chain runbook](runbooks/audit-chain.md).
