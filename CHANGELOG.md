# Changelog

## Unreleased — team console (milestone 5)

- Bundled React/Vite console at `/console`: existing team-key/JWT sign-in, in-memory identity
  switching, first-run guidance, filtered runs, start/cancel/retry, receipt and gateway-log
  timelines, and verified approve/reject actions across available projects.
- Admin provider key-presence checks and reviewed configuration guidance; shared budgets
  and current-window costs by team, provider, and workflow. No secret values leave the gateway.
- Same gateway image and Helm switch; Compose walkthrough now checks the main pages in
  headless Chromium. Added browser and API regressions, responsive layout, keyboard navigation,
  no-store responses, and a same-origin console content security policy.
- React/ReactDOM are the only UI runtime dependencies; Vite, TypeScript, and Playwright provide
  builds and browser verification. Node.js 24 is build/test-only, absent from the runtime image.

## v0.2.0 - 2026-10-07

- Projects and admin/builder/approver/viewer roles on existing sandbox credentials;
  per-team provider secrets, shared USD limits, and cross-provider usage reports.
- Authenticated workflow API and CLI: start, list, inspect, cancel, retry, and approve;
  verified reviewer identities and step timelines with provider, cost, and receipt IDs.
- Grafana team operations dashboard and alerts. Cloud-first Compose smoke checks roles,
  isolation, timeline, spend, cancellation/retry, and worker recovery against local fakes.
- Team-lead documentation and version metadata. The gateway adds the Temporal client
  dependency to control the existing workflow service. No tag or publication is implied.

### Durable workflows and agent integrations (milestones 2–3)

- Framework adapters and runnable OpenAI, Anthropic, OpenAI Agents SDK, and LangGraph examples
  using the governed gateway within Temporal activities; optional framework dependencies.
- Central team MCP Streamable HTTP registrations, workflow tool allowlists, argument/output DLP,
  bounded responses, per-call costs, and correlated receipts on the existing governance path.
- Workflow policies for providers, models, tools, egress origins, and immutable run budgets.
- Container activities in existing agent-sandbox workspaces, with isolation checks, narrow worker
  RBAC, exclusive execution, expiring scoped credentials, revocation, and result receipts.
- Extended Compose smoke and workflow/workspace guides; regression tests use only local fakes.

- Temporal-backed Python workflows with governed model/tool activities, retries, timeouts,
  per-run token/cost budgets, provider fallback, approval signals, and run-linked receipts.
- Self-hosted Temporal, dedicated PostgreSQL, persistent budget Redis, and a worker in
  Compose and Helm/GitOps using the official Temporal chart.
- Research → draft → approval → publish example; Compose smoke kills its worker and verifies
  recovery without repeating completed model calls. Added workflow SDK and gateway tests.
- Ten-minute workflow guide covering tool policies, deployment, and recovery boundaries.

## v0.1.0 - 2026-10-07

First AgentWorkflows product version, forked from private-ai-platform-kit with full Git history.

- Governed OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, and Vertex Gemini routes, with
  classification-aware fallback, server-side credentials, usage accounting, and receipts.
- AgentWorkflows naming across the Python distribution and import package, CLI, images,
  Compose project, Helm chart, documentation, and CI.
- A cloud-first local walkthrough using protocol fixtures, with self-hosted models optional.
- Retained gateway, retrieval, sandbox, budget, and audit controls; removed the kit's research
  publication and inherited release evidence. Sample reports remain format examples only.
- Durable workflows and human approvals remain planned; they are not part of this version.

The upstream kit's releases and research remain in its separate repository and this fork's history.
