# AgentWorkflows roadmap

AgentWorkflows is the agent workflow platform for teams: governed cloud providers, durable
agent workflows, human approvals, team budgets, and receipts for model and tool calls.
Version 0.4.0 adds the TypeScript SDK, a clearer console and published release artifacts.
The v0.2.0 tag includes milestones 1–7, v0.3.0 delivers milestones 8–9, and v0.4.0
delivers milestone 10.

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

## Milestone 6: guided onboarding — delivered in 0.2.0

- Guided Bash and Windows PowerShell quickstart and editable research, support-triage,
  and code-review templates through `agentworkflows init`.

## Milestone 7: production hardening — delivered in 0.2.0

- Versioned gateway Redis state and Temporal SQL schemas; previous-release upgrade drills.
- Quiesced backups of gateway state, Temporal history/visibility and receipts, with verified restore drills.
- Gateway/console and worker replica values, disruption budgets, dependency probes and graceful draining.
- Explicitly opted-in, environment-keyed provider acceptance, disabled in CI; concurrent workflow accounting tests.

See [production readiness](docs/production-readiness.md) for commands and evidence limits.

## Milestone 8: automatic starts and team notifications — delivered in 0.3.0

- Per-workflow UTC cron triggers backed by Temporal Schedules, and signed inbound
  JSON webhooks with replay protection and the existing team/project governance.
- Console and CLI trigger discovery and pause/resume; trigger and delivery receipts.
- Slack incoming webhooks, outgoing webhooks and SMTP alerts for waiting approvals,
  failed runs and configurable run budget thresholds, linking back to console review.
- Daily report and GitHub issue payload triage examples in the Compose trial, with
  a local notification sink and SMTP catcher. No real credentials or chat approvals.

See [triggers and notifications](docs/workflows.md#triggers-and-notifications) for
signing, delivery guarantees, configuration and the smoke proof.

## Milestone 9: workflow templates and documentation — delivered in 0.3.0

- Five editable team templates: PR review with human approval, support triage, weekly
  reports, incident summaries, and document Q&A with citations.
- Credential-free Compose walkthroughs with inputs, expected results, and adaptation guidance.
- Docs landing page and a side-by-side comparison with LiteLLM plus Temporal.

See the [template gallery](docs/templates.md) and [decision guide](docs/decision-guide.md).

## Milestone 10: TypeScript SDK and console polish — delivered in 0.4.0

- TypeScript workflow SDK with governed activities, approvals and two runnable examples.
- Readable run timelines, explained fallback costs, result summaries and phone layouts.
- Tag-only release workflow publishing signed images and charts plus both SDKs.

## Next: deployment validation and integration coverage

Deployment-specific identity, networking, storage failover and retention remain operator
validation work, together with live-provider acceptance and recovery evidence on real data.
Broader instrumented integrations and measurable receipt coverage remain ongoing: the audit
chain cannot prove unreported tool activity or direct calls outside the gateway. Fixture
walkthroughs demonstrate governance and execution, not real-provider or model-quality acceptance.

## Usability and release standard

Every capability needs sensible defaults, one obvious path, actionable errors, and
copy-pasteable examples a team lead can follow in ten minutes. Prefer removing friction
to adding options. Keep provider compatibility, upgrade checks, and current release
evidence reproducible. Dates and future release numbers are not promised here.

AgentWorkflows is cloud-first. Its upstream kit continues separately as a self-hosted
project with its own research publication. See [scope](docs/scope-and-non-goals.md)
for current boundaries and [CHANGELOG.md](CHANGELOG.md) for product releases.
