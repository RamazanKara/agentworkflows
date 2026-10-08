Repository: RamazanKara/agentworkflows, branch main. Pull first; commit and push straight to main when green. Read docs/PRODUCT-GAPS.md (gaps 5, 6 and 9) before starting. Another change (identity: OIDC sessions and managed keys) may land on main while you work; rebase on it, don't revert it.

Goal: let a team see what each workflow step actually did, start any workflow from a generated form, and stop run records from living forever.

1. Step input/output capture (gateway + both SDKs):
   - New per-workflow policy field `captureContent: none|redacted|full` (default `redacted` in the Compose demo policy, `none` when absent) in the SandboxPolicySet workflow policy (app/policy.py WorkflowPolicy) and team-level default.
   - When enabled, the governed model and tool activities record the request messages/arguments and the response text/result for each step, after the existing DLP/redaction (`redacted` applies the gateway's existing redaction; `full` stores as-is). Truncate each field to a configurable size (default 16 KB) and mark truncation.
   - Store it in Redis keyed by run and step with a TTL (`CONTENT_RETENTION_SECONDS`, default 7 days), never in receipts (receipts keep only fingerprints, as today).
   - Expose it on `GET /v1/workflow-runs/{id}` steps as `content: {input, output, truncated, redaction}` only to roles that can read the run; return `content: null` with a reason when capture is off or expired.

2. Workflow input schemas:
   - Python SDK and TS SDK: a workflow can declare an input schema (JSON Schema draft 2020-12 subset: object with string/number/integer/boolean/enum/array-of-string properties, required, description, default, examples).
   - Policy: optional `inputSchema` per workflow in the SandboxPolicySet; `GET /v1/workflow-policies` returns it. Validate `POST /v1/workflow-runs` input against it and return 422 with field-level messages.
   - Add schemas for all bundled templates (sdk/python/agentworkflows/examples and the TS examples) and the Compose demo policy, and remove the hardcoded input field names in app/workflow_operations.py where the schema now covers it.
   - `agentworkflows init` writes the schema into the generated project.

3. Run records retention: add `RUN_RECORD_RETENTION_SECONDS` (default 30 days) applied as a TTL to run record keys after a run reaches a terminal state; include a state migration that sets TTLs on existing terminal runs. Document in runbooks/data-retention.md.

4. Console (src/inference-gateway/console), minimal wiring, the design pass happens afterwards:
   - Run detail: each step expands to show input and output (pre-wrapped, escaped text, never HTML), with the redaction and truncation notes.
   - Run workflow page: render a form from inputSchema (text, textarea for long strings, number, checkbox, select for enum, one-per-line for string arrays), falling back to the JSON box only when a workflow has no schema. Delete the hardcoded suggestedInput examples.
   - Playwright tests with route mocks for both.

5. Tests: gateway unit tests for capture modes, TTL, role visibility, schema validation and 422 shape; SDK tests for schema declaration. `make lint` and `make test-gateway` must pass. Update docs/workflows.md, docs/sdk-reference.md, docs/quickstart.md where the run page or inputs are described, and CHANGELOG.md under "v0.5.0 - unreleased".

Report: what changed, test output summary, anything not verified.
