Repository: RamazanKara/agentworkflows, branch main. Pull first; commit and push straight to main when green. Read docs/PRODUCT-GAPS.md (gap 8) before starting. Other changes (console polish, an audit log job) may land on main while you work; rebase on them, don't revert them.

Goal: a team admin changes budgets, approval rules and model routing in the console instead of editing YAML and restarting the gateway. Provider API keys stay in Kubernetes Secrets or environment variables; never store them in Redis.

1. Server-side settings store (gateway):
   - New module app/team_settings.py: per-team overrides stored in Redis (one hash or JSON document per team, with a `revision` integer and `updated_by`/`updated_at`), layered over the SandboxPolicySet YAML, which stays the default and the bootstrap.
   - Overridable fields: team monthly budget, per-project budgets, per-workflow budget and approval requirement (approval threshold, approver role), allowed providers per workflow, and the model route chosen for each model alias (only among routes that already exist in model-routing.yaml; routes themselves stay YAML).
   - Every read path that uses these values today (team_budget.py, workflow_budget.py, admission, runtime_routing, GET /v1/team, GET /v1/workflow-policies) reads the effective value through one function. No restart needed; cache for at most a few seconds.
   - Each change emits a chained audit event (`team_settings_changed`, before/after values, actor) through the existing audit chain.

2. Admin API:
   - `GET /v1/team/settings`: effective values plus, per field, `source: policy|override` and the policy default.
   - `PATCH /v1/team/settings` with `If-Match: <revision>` (409 on a stale revision); validate types, non-negative budgets, routes that exist, approver roles that exist. 422 with field-level messages.
   - `DELETE /v1/team/settings/{field}` resets one field to the policy default.
   - Admin role only; other roles get 403 with the existing `team_role_required` shape. Add to the OpenAPI contract (make api-contract).

3. Console (src/inference-gateway/console), minimal wiring; the design pass and screenshot gate happen afterwards in the cloud:
   - Replace the "paste this YAML" budget and routing instructions on the Team/Providers/Costs pages with editable fields for admins (read-only for other roles), a Save button, a "Reset to policy default" link per overridden field, and a conflict message on 409.
   - Playwright tests with route mocks: edit and save, stale revision, reset, non-admin read-only.

4. Tests: gateway unit tests for layering, revision conflicts, validation, role checks, audit events, and that budget enforcement and routing pick up a change without restart. `make -j2 lint test-scripts test-gateway` and `make api-contract config-contract` must pass. Document in docs/ (team settings page) and runbooks, and add a CHANGELOG entry under "Unreleased".

Report: what changed, test output summary, anything not verified.
