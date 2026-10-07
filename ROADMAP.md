# AgentWorkflows roadmap

AgentWorkflows is the agent workflow platform for teams: governed cloud providers, durable
agent workflows, human approvals, team budgets, and receipts for model and tool calls.
Version 0.2.0 is the first public release and includes milestones 1–5 plus the guided
quickstart and project templates. Production hardening evidence is tracked below.

## Milestone 1: governed cloud providers — delivered in 0.2.0

OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, and Vertex Gemini share authentication,
model policies, classification-aware fallback, token budgets, usage accounting, and audit
receipts. Ollama and vLLM remain optional self-hosted backends. The local walkthrough
uses provider protocol fixtures; live provider acceptance is deployment-specific.

## Milestone 2: durable agent workflows — delivered in 0.2.0

- Temporal with dedicated PostgreSQL, Compose, and an official-chart-based GitOps deployment.
- Python workflow SDK: governed model/tool activities, backoff, timeouts, per-run budgets,
  provider fallback, and human-approval signals.
- Run/step receipt correlation and a worker-SIGKILL recovery test with cloud fixtures.

The existing Batch API and stored Responses are not a durable workflow engine.

## Milestone 3: bring your agents — delivered in 0.2.0

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

## Milestone 5: team web console — delivered in 0.2.0

- Same-origin React console bundled with the gateway in Compose and Helm.
- Existing team auth, verified identity switching, filtered runs, receipt timelines, and approvals.
- First-run walkthrough, admin provider/budget configuration guidance, and team/provider/workflow costs.
- Responsive, keyboard-accessible pages and headless browser checks in the Compose walkthrough.
- Guided Bash and Windows PowerShell quickstart and editable research, support-triage,
  and code-review templates through `agentworkflows init`.

## Milestone 6: production hardening and broader coverage

- Versioned gateway Redis state and Temporal SQL schemas; previous-release upgrade drills.
- Quiesced backups of gateway state, Temporal history/visibility and receipts, with verified restore drills.
- Gateway/console and worker replica values, disruption budgets, dependency probes and graceful draining.
- Explicitly opted-in, environment-keyed provider acceptance, disabled in CI; concurrent workflow accounting tests.

See [production readiness](docs/production-readiness.md) for commands and evidence limits.
Deployment-specific identity, networking, storage failover and retention remain operator
validation work. Broader instrumented integrations and measurable receipt coverage remain
ongoing: the audit chain cannot prove unreported tool activity or direct calls outside the gateway.

## Usability and release standard

Every capability needs sensible defaults, one obvious path, actionable errors, and
copy-pasteable examples a team lead can follow in ten minutes. Prefer removing friction
to adding options. Keep provider compatibility, upgrade checks, and current release
evidence reproducible. Dates and future release numbers are not promised here.

AgentWorkflows is cloud-first. Its upstream kit continues separately as a self-hosted
project with its own research publication. See [scope](docs/scope-and-non-goals.md)
for current boundaries and [CHANGELOG.md](CHANGELOG.md) for product releases.
