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
[scope](scope-and-non-goals.md)), a visual workflow builder (workflows are code by design).

Status: 1–7 shipped in v0.5.0 (2026-10-08). Gaps 8 and 10 are implemented for v0.6.0 (unreleased): revision-checked team settings and the retained audit viewer, verification and export. Run retention shipped in v0.5.0; v0.6.0 adds stable run cursors and full-detail JSON Lines export. The v0.6.x storage work adds optional PostgreSQL for gateway run history, audit, settings and API-key metadata. Redis remains the default and still handles live accounting and sessions; Temporal retains its separate PostgreSQL execution store. See [gateway storage](postgresql-storage.md) for cutover and validation limits.

Codex job prompts live in `docs/codex/` (not published on the docs site).

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
