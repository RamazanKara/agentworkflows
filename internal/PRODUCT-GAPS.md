# Product gaps (audit 2026-10-08, after v0.4.0)

What a team evaluating AgentWorkflows hits first, ranked by how hard it blocks adoption.
The governance core (budgets, approvals, receipts, roles, durable runs) works. The product
layer around it is thin: identity, membership, configuration and install are YAML + redeploy.

| # | Gap | Today | Target for v0.5.0 | Owner |
|---|-----|-------|-------------------|-------|
| 1 | **Real sign-in** | Console asks users to paste a raw API key or JWT; a reload signs them out | "Sign in with your company account" (OIDC code flow + PKCE), HttpOnly session cookie that survives reloads, sign-out; API keys stay for automation | Codex job A + console |
| 2 | **Members and API keys in the product** | New member = generate key, hash it, edit key-records YAML, restart gateway | Admin API + console page: invite/create key with role and project, list with last-used, revoke, expiry; keys stored in Redis next to existing state; YAML stays as bootstrap | Codex job A + console |
| 3 | **One-command install** | `helm install` omits Temporal and the worker; Compose builds images locally | Umbrella chart includes the `workflows` chart; Compose pulls the published GHCR images by default | Codex job C (Helm, verified on kind); Compose images: cloud |
| 4 | **Installable SDKs** | Docs install from a local checkout; PyPI publish gated off; no npm publish | Install from the GitHub release today; PyPI and npm publish once the owner registers the trusted publisher / npm token | Cloud + owner |
| 5 | **See what each step did** | Step timeline shows cost, tokens and receipt hashes, but not the prompt or answer | Opt-in, redacted step input/output capture with a retention TTL, shown on the run page | Codex job B + console |
| 6 | **Workflow inputs as forms** | Only the Research example has a form; others get a raw JSON box with hardcoded examples | Workflows declare a JSON input schema (SDK + policy); console renders a form from it | Codex job B + console |
| 7 | **Docs a newcomer can follow** | Nav mixes the product with legacy platform-kit material (GPU tenants, regulated offline, OWASP crosswalk); no Kubernetes install page | Product-first nav, legacy pages under "Advanced operations", a Helm install guide | Cloud |
| 8 | **Configure in the console** | Provider keys and budgets: console prints YAML to paste | Admin edits team budgets and provider routing in the console (stored server-side, YAML as defaults) | Codex job D + console (v0.6.0) |
| 9 | **Durable system of record** | Run records and budgets live only in a single Redis, no TTL on run records | Run-record retention TTL now; Postgres store considered for v0.6.0 | TTL: Codex job B; Postgres later |
| 10 | **Audit log viewer** | Audit chain exists, no console view | Read-only audit page for admins, with chain verification | Codex job E + console (v0.6.0) |

Deliberately not planned: hosted SaaS, billing and plans (self-hosted product; see
[scope](../docs/scope-and-non-goals.md)), a visual workflow builder (workflows are code by design).

Status: 1–7 shipped in v0.5.0 (2026-10-08). Gaps 8 and 10 are implemented for v0.6.0 (unreleased): revision-checked team settings and the retained audit viewer, verification and export. Run retention shipped in v0.5.0; v0.6.0 adds stable run cursors and full-detail JSON Lines export. The v0.6.x storage work adds optional PostgreSQL for gateway run history, audit, settings and API-key metadata. Redis remains the default and still handles live accounting and sessions; Temporal retains its separate PostgreSQL execution store. See [gateway storage](../docs/postgresql-storage.md) for cutover and validation limits.


The next v0.6.x pass closes the remaining console paths within gaps **2** and **5**:
edit existing key names, roles, projects and expiry; change team/workflow content
capture without redeployment. These were chosen because day-to-day access changes
and inspecting step content otherwise still required operator work. Both use the
existing administration APIs, now with matching SDK helpers, console controls and
tests. The same pass adds offline Redis-to-PostgreSQL import for gap **9**, and
soft/hard monthly spend limits with in-console and webhook alerts for gap **8**.
Live cutover, identity-provider administration and provider-secret provisioning
remain operator responsibilities.

## v0.7.0: automated run visibility and spend reporting (unreleased)

The next coherent step is to make scheduled work inspectable and team spend portable.
Cron/webhook starts, approval expiry and console cancellation/retry already work; they
are not new v0.7.0 features. This milestone completes two remaining product paths:

| Gap | v0.7.0 implementation | Boundary |
| --- | --- | --- |
| Trigger run history | Console Run history links; persisted launch provenance; workflow/trigger filters in the API, CLI and both SDKs | New launches only; existing run retention applies; rejected deliveries remain audit events |
| Usage CSV export | Costs download, API, Python and TypeScript helpers, CLI output | Current UTC month, configured price estimates and reservations; project-bound credentials export only their project |

Both paths share existing storage and authorization. No new service or Helm switch
is needed. One-click/versioned template installation, multi-approver escalation,
per-workflow secrets and SSO group mapping remain open. Broader SDK parity and live
OpenTelemetry/deployment acceptance remain integration work. There is no hosted SaaS.

## v0.8.0: company group access for teams (unreleased)

Selected scope: **SSO group-to-role mapping and access inspection**. Team onboarding
already has OIDC, scoped roles and sessions, but requires the identity provider to
emit a gateway-specific scalar role. Mapping existing company groups removes that
adoption step while keeping policy under the deployment operator's control.

The gateway now accepts validated, team-scoped group mappings through Helm or
environment configuration. Exactly one distinct mapped role is required; unmatched
or conflicting groups fail closed. Group sessions expire with the ID token and
affected policy changes require sign-in again. An admin-only API, both SDKs and
Members & keys show this team's effective sign-in policy without exposing secrets
or other teams' groups. Empty mappings preserve the current role-claim path.

The acceptance boundary is signed-token fixture tests, native contract/SDK/console
checks and Helm rendering, plus the documented source-build Quickstart when Docker
is available. Real identity-provider configuration and deployment acceptance remain
operator tasks. One-click/versioned templates, multi-approver escalation,
per-workflow secrets, broader SDK parity and live trace export remain open. Cron,
approval expiry, console cancellation/retry, trigger history and CSV export were
already implemented and are not counted again in this milestone.


## v0.9.0: shared approval decisions (unreleased)

Selected scope: **multi-approver approval policies with configurable expiry**. This
closes the team-review gap using the existing Temporal approval gate, revision-checked
settings and authenticated reviewer identities. No new service or dependency is needed.

A run saves its approval rules at launch. One to ten distinct identities must approve;
each can vote once, any rejection ends the gate, and expiry is configurable from one
minute to seven days. Python and TypeScript implement the same behavior; the console
edits the policy and displays quorum progress and deadlines. Helm uses the existing
workflow policy values. The default remains one reviewer and seven days.

Acceptance includes API/worker contracts, duplicate delivery, role and project boundaries,
expiry/rejection, legacy worker compatibility, SDK and console tests, and source-build
Quickstart verification where Docker is available. Escalation, versioned one-click
installation and per-workflow secrets remain open. OpenTelemetry live export and broader
SDK parity remain integration work. Already-implemented features are not counted again.

## 1.0.0-rc.4 adoption pass — plan recorded before implementation

Existing behavior is the baseline, not a new feature claim. Work proceeds in this order.
Each gap includes the HTTP contract, gateway behavior, console, Python/TypeScript methods,
documentation, and regression coverage. Container acceptance is separate from native checks.

| Gap | Symptom for a new team | Why it blocks adoption | Plan | Status |
| --- | --- | --- | --- | --- |
| Onboarding | Get started is a static checklist; a missing provider key sends an admin to shell commands. | A team cannot reach its first approved result from the product. | Add a readiness contract and live wizard, choose a ready sample model, securely save a missing provider key using existing encryption, install/start the sample, and link review and evidence. | Implemented; onboarding API, SDK and browser checks passed. Container acceptance pending. |
| Templates | Six templates; installation requires preapproved worker policy. | Common team jobs still start from a blank file. | Expand to nine distinct, documented workflows, register workers and policies, retain version-pinned idempotent installation, and cover schemas, outputs, and approval decisions. | Implemented; nine templates with Python/TypeScript workflow, scaffold, and gateway version checks passing. |
| Auth and teams | Members are effectively API-key records; no invitation lifecycle. | Admins hand credentials around and cannot track pending access. | Add expiring, single-use invitation creation/acceptance/revocation, role/project boundaries and audit; console copy/accept flows; preserve OIDC mapping and managed-key controls. | Implemented; gateway identity suite passed (117 tests). Invitation SDK and console acceptance covered. |
| Observability | Step timings and costs exist, but notification rules are deployment policy only. | Teams cannot choose actionable run alerts without operator changes. | Add revision-checked alert rules for existing approved notification channels, console controls, delivery/error visibility, SDK parity and tests; retain the run timeline. | Implemented: revisioned alert API and SDKs, console rules, safe failure surfaces; 22 alert/trigger and 85 TypeScript SDK tests pass. Live delivery acceptance pending; final TypeScript suite: 86 tests. |
| Deployment | Compose, hardened Helm settings, and a kind trial exist in separate guides. | Teams cannot identify one supported path or recover an installation confidently. | Consolidate one-command source Compose, validate a single-tenant Helm reference, expose deployment readiness, and document backup/restore and ordered upgrades with explicit caller acceptance commands. | Implemented: configuration API, console checklist, both SDKs, single-tenant Helm profile and recovery/upgrade guide. Native Helm rendering passed (20 tests, 8 subtests); Compose/kind and managed-service drills require the caller. |

Final rc.4 native verification (after rebasing onto rc.3): gateway 1,139 passed/18 skipped; Python SDK 219 passed/3 skipped; TypeScript 86 passed; console build and 82 tests passed. Changed Python files and contracts pass. Container/live-provider/recovery acceptance remains open, and existing repository Ruff/format/MkDocs failures remain unchanged. See [verification and caller commands](../docs/release-verification.md#rc4-adoption-pass).

## 1.0.0-rc.5 adoption pass — plan recorded before implementation

The rc.4 pass closed first-run, templates, invitations, alert rules and deployment readiness.
A review of the code path a developer takes after the sample run found the next blockers.
Each gap below was written here before it was built, ranked by how early it stops a team.

| Gap | Symptom for a new team | Why it blocks adoption | Plan | Status |
| --- | --- | --- | --- | --- |
| Bring your own workflow | A developer writes a workflow and a worker, but the gateway refuses it as `workflow_not_allowed` until an operator adds a `workflows:` block to the YAML or Helm values and restarts the gateway. Only the nine bundled templates can be used from the product. | The headline promise, "turn your script into a durable, governed job", ends at a ticket to the platform team. Developers evaluating the product stop at their first custom workflow. | Let a team admin register a workflow type from the console, API, CLI and both SDKs. Registration can only narrow the operator's envelope: models, providers and tools must already be approved for the team; egress is derived from those routes and tools; limits are capped by the team's own limits; operator-defined and built-in names are reserved. Revision-checked, audited, removable, and read through the same effective policy as YAML workflows. The SDKs can register straight from a workflow class and its input schema. | Implemented: team workflow registry API, console, CLI, Python and TypeScript SDKs, operator opt-out and audit. Native gateway, SDK and browser checks pass; container acceptance pending. |
| Run insights | The run page lists each step's cost and latency, and Costs shows monthly spend by provider and workflow. Nothing says how long a run took end to end, how long it waited for a reviewer, which step is slow or expensive, or whether a workflow is succeeding, failing or being rejected more over the past week. | A team lead cannot judge whether a workflow is worth keeping, tune its budget or reviewers, or notice a regression without reading runs one by one. Alerts say something broke, not whether the workflow is healthy. | Add a per-run summary (elapsed time, model and tool time, review wait, cost and tokens by step) to the run API and page, and a retained-run insights API with per-workflow outcomes, median and 95th-percentile duration, review wait, average cost and the slowest and costliest step over 1, 7 or 30 days. Add an Insights console page, Python and TypeScript methods, a CLI command, and tests with fixed fixtures. Derive everything from retained runs and receipts already stored; scan at most 500 runs and say when the window is truncated. | Implemented: run summary on the run API and page, workflow insights API, Insights page, CLI and both SDKs. Native gateway, SDK and browser checks pass; Temporal-backed accuracy pending container acceptance. |
| Acceptance check for your own install | The console's deployment checklist tells an operator to "run the first-approved-run check after every install and upgrade", but `scripts/first-approved-run.py` can only create its own disposable Compose or kind stack, and the single-tenant guide offers only that disposable kind trial. A team that installed with Helm on its own cluster has no supported way to prove its install works end to end, or to time zero-to-first-approved-run for its people. | Operators cannot confirm a new install, an upgrade or a restored backup actually completes a governed run, so they fall back to clicking through the console by hand, or skip it. The five-minute promise is unmeasurable where it matters. | Add `agentworkflows check` (Python CLI and SDK, and a TypeScript method) that runs the console wizard's path against any gateway: read readiness and report the same blockers the console shows, install the sample if the caller may, start it with the sample input, wait for the draft, approve it with a reviewer credential (a separate one when you have it), confirm completion, the approval receipt, the run summary and, for admins, audit-chain verification. It prints per-stage timings against a 300-second budget, refuses to spend on a real provider without `--allow-paid`, never takes keys on the command line, and has `--json` for CI. Point the deployment checklist, single-tenant guide, quickstart and release checklist at it; keep the Compose and kind trial script for disposable stacks. | Implemented: `agentworkflows check` and Python/TypeScript functions, deployment checklist command, single-tenant, quickstart and SDK docs. Native tests use a fake gateway; a live run against Compose or Helm requires the caller. |

Final rc.5 native verification: gateway 1,163 passed/14 skipped; Python SDK 242 passed/3 skipped; TypeScript 93 passed; console build and 91 browser tests passed; OpenAPI, configuration and chart-docs contracts passed. Compose, Helm, Docker and live-provider acceptance remain open (no Docker or working WSL was available). See [verification and caller commands](../docs/release-verification.md#rc5-adoption-pass).

Next open gaps, in rough order of value: a hardened single-host Compose profile with generated secrets, TLS, backup/restore and upgrade scripts; API-key rotation with overlap, session listing and member offboarding; team-registered triggers and container agents (still operator YAML); regression alerts on Insights (failure rate, p95 time) and per-member spend; auto-registration of a workflow when its worker starts.
