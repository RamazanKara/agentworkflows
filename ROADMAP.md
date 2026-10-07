# AgentWorkflows roadmap

AgentWorkflows is the agent workflow platform for teams: governed cloud providers, durable
agent workflows, human approvals, team budgets, and receipts for model and tool calls.
Version 0.2.0 delivers milestones 1–4. The remaining milestones are planned.

## Milestone 1: governed cloud providers — delivered

OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, and Vertex Gemini share authentication,
model policies, classification-aware fallback, token budgets, usage accounting, and audit
receipts. Ollama and vLLM remain optional self-hosted backends. The local walkthrough
uses provider protocol fixtures; live provider acceptance is deployment-specific.

## Milestone 2: durable agent workflows — delivered in this checkout

- Temporal with dedicated PostgreSQL, Compose, and an official-chart-based GitOps deployment.
- Python workflow SDK: governed model/tool activities, backoff, timeouts, per-run budgets,
  provider fallback, and human-approval signals.
- Run/step receipt correlation and a worker-SIGKILL recovery test with cloud fixtures.

The existing Batch API and stored Responses are not a durable workflow engine.

## Milestone 3: bring your agents — delivered in this checkout

- OpenAI/Anthropic clients, OpenAI Agents SDK, and LangGraph as governed Temporal steps.
- Team MCP registrations with explicit tool allowlists, argument DLP, costs, and receipts.
- Container steps in existing hardened agent-sandbox workspaces with expiring run credentials.
- Per-workflow provider, model, tool, egress, and budget policy in the existing team configuration.

Compose exercises framework and MCP flows with local fakes. Container execution requires
the existing Kubernetes workspace runtime and an enforcing NetworkPolicy CNI.

## Milestone 4: operate as a team — delivered in 0.2.0

- Projects and admin, builder, approver, and viewer roles on existing sandbox identities.
- Per-team provider secrets, shared token and USD limits, and provider usage reports.
- Authenticated start/list/inspect/cancel/retry/approve API and CLI; verified approval identities.
- Run step timelines with provider, model, tokens, cost, duration, and receipt IDs.
- Grafana team dashboard and alerts for throughput, failures, approvals waiting, and spend.
- A cloud-first Compose workflow trial using local fakes; Ollama remains opt-in.

Team onboarding uses the existing reviewed key and sandbox policy files. Cost reports use
configured prices and conservative reservations, not provider invoices. Temporal access
and workers are trusted operator surfaces; end users use the authenticated gateway.

## Milestone 5: production hardening — planned

Strengthen upgrade, backup, availability, and live-provider acceptance evidence. Validate
team operations under deployment-specific identity, networking, and retention policies.

## Milestone 6: broader workflow coverage — planned

Expand instrumented model/tool integrations and measurable receipt coverage. The current
audit chain cannot prove unreported tool activity or direct calls outside the gateway.

## Usability and release standard

Every capability needs sensible defaults, one obvious path, actionable errors, and
copy-pasteable examples a team lead can follow in ten minutes. Prefer removing friction
to adding options. Keep provider compatibility, upgrade checks, and current release
evidence reproducible. Dates and future release numbers are not promised here.

AgentWorkflows is cloud-first. Its upstream kit continues separately as a self-hosted
project with its own research publication. See [scope](docs/scope-and-non-goals.md)
for current boundaries and [CHANGELOG.md](CHANGELOG.md) for product releases.
