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

Status: 1–7 shipped in v0.5.0 (2026-10-08) after the console passed the screenshot gate on 2024 phones and desktop. 8–10 are next: Codex job D (team settings in the console) and E (audit log viewer), then the Postgres store.

Codex job prompts live in `docs/codex/` (not published on the docs site).
