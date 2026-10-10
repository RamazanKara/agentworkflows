# AgentWorkflows roadmap

AgentWorkflows is the agent workflow platform for teams: governed cloud providers, durable
agent workflows, human approvals, team budgets, and receipts for model and tool calls.
The v0.2.0 tag includes milestones 1–7, v0.3.0 delivers milestones 8–9, v0.4.0 delivers
milestone 10, v0.5.0 delivers milestone 11, and v0.9.0 delivers milestones 12–16.
The [next section](#next) lists the work that comes after v0.9.0.

## Milestone 1: governed cloud providers (delivered in 0.2.0)

OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, and Vertex Gemini share authentication,
model policies, classification-aware fallback, token budgets, usage accounting, and audit
receipts. Ollama and vLLM are optional self-hosted backends. The local walkthrough
uses provider protocol fixtures; each deployment connects its own provider accounts.

## Milestone 2: durable agent workflows (delivered in 0.2.0)

- Temporal with dedicated PostgreSQL, Compose, and an official-chart-based GitOps deployment.
- Python workflow SDK: governed model/tool activities, backoff, timeouts, per-run budgets,
  provider fallback, and human-approval signals.
- Run/step receipt correlation and a worker-SIGKILL recovery test with cloud fixtures.

## Milestone 3: bring your agents (delivered in 0.2.0)

- OpenAI/Anthropic clients, OpenAI Agents SDK, and LangGraph as governed Temporal steps.
- Team MCP registrations with explicit tool allowlists, argument DLP, costs, and receipts.
- Container steps in existing hardened agent-sandbox workspaces with expiring run credentials.
- Per-workflow provider, model, tool, egress, and budget policy in the existing team configuration.

Compose exercises framework and MCP flows with local fakes. Container execution runs on
the Kubernetes workspace runtime with an enforcing NetworkPolicy CNI.

## Milestone 4: operate as a team (delivered in 0.2.0)

- Projects and admin, builder, approver, and viewer roles on existing sandbox identities.
- Per-team provider secrets, shared token and USD limits, and provider usage reports.
- Authenticated start/list/inspect/cancel/retry/approve API and CLI; verified approval identities.
- Run step timelines with provider, model, tokens, cost, duration, and receipt IDs.
- Grafana team dashboard and alerts for throughput, failures, approvals waiting, and spend.
- A cloud-first Compose workflow trial using local fakes; Ollama is opt-in.

Cost reports use configured prices and conservative reservations. Temporal access and
workers are operator surfaces; end users work through the authenticated gateway.

## Milestone 5: team web console (delivered in 0.2.0)

- Same-origin React console bundled with the gateway in Compose and Helm.
- Existing team auth, verified identity switching, filtered runs, receipt timelines, and approvals.
- First-run walkthrough, admin provider/budget configuration guidance, and team/provider/workflow costs.
- Responsive, keyboard-accessible pages and headless browser checks in the Compose walkthrough.

## Milestone 6: guided onboarding (delivered in 0.2.0)

- Guided Bash and Windows PowerShell quickstart and editable research, support-triage,
  and code-review templates through `agentworkflows init`.

## Milestone 7: production hardening (delivered in 0.2.0)

- Versioned gateway Redis state and Temporal SQL schemas; previous-release upgrade drills.
- Quiesced backups of gateway state, Temporal history/visibility and receipts, with verified restore drills.
- Gateway/console and worker replica values, disruption budgets, dependency probes and graceful draining.
- Explicitly opted-in, environment-keyed provider acceptance; concurrent workflow accounting tests.

See [production readiness](docs/production-readiness.md) for commands and evidence.

## Milestone 8: automatic starts and team notifications (delivered in 0.3.0)

- Per-workflow UTC cron triggers backed by Temporal Schedules, and signed inbound
  JSON webhooks with replay protection and the existing team/project governance.
- Console and CLI trigger discovery and pause/resume; trigger and delivery receipts.
- Slack incoming webhooks, outgoing webhooks and SMTP alerts for waiting approvals,
  failed runs and configurable run budget thresholds, linking back to console review.
- Daily report and GitHub issue payload triage examples in the Compose trial, with
  a local notification sink and SMTP catcher.

See [triggers and notifications](docs/workflows.md#triggers-and-notifications) for
signing, delivery guarantees, configuration and the smoke proof.

## Milestone 9: workflow templates and documentation (delivered in 0.3.0)

- Five editable team templates: PR review with human approval, support triage, weekly
  reports, incident summaries, and document Q&A with citations.
- Credential-free Compose walkthroughs with inputs, expected results, and adaptation guidance.
- Docs landing page and a side-by-side build-it-yourself comparison.

See the [template gallery](docs/templates.md) and [decision guide](docs/decision-guide.md).

## Milestone 10: TypeScript SDK and console polish (delivered in 0.4.0)

- TypeScript workflow SDK with governed activities, approvals and two runnable examples.
- Readable run timelines, explained fallback costs, result summaries and phone layouts.
- Tag-only release workflow publishing signed images and charts plus both SDKs.

## Milestone 11: a product teams can adopt (delivered in 0.5.0)

- Company sign-in (OIDC) with persistent browser sessions; members and API keys managed
  in the console, CLI and API instead of YAML.
- Run forms generated from each workflow's input schema; each step's prompt and answer,
  with redaction and a retention limit; run records expire on a schedule.
- One-command Kubernetes install that includes Temporal and the worker; Compose and the
  SDKs install from published release artifacts.
- Product-first docs.

## Milestone 12: team administration and retained history (delivered in 0.9.0)

- Edit monthly team/project budgets, workflow approvals and approved model routes in
  the console, API and SDKs, with revision checks and change receipts.
- Read, verify and export the retained team audit view in the console, CLI and SDKs.
- Page run history and approvals with stable cursors; export retained run details as
  JSON Lines using either SDK or the CLI. Existing offset clients remain compatible.
- Production Helm controls for TLS ingress, HA, NetworkPolicies and external stores.
- Offline, resumable Redis-to-PostgreSQL import with audit verification; monthly
  soft/hard spend limits and webhook/in-console alerts; existing-key access editing
  and runtime step-content capture settings in the console and SDKs.

This milestone adds an optional [PostgreSQL gateway store](docs/postgresql-storage.md)
for run history, audit events and heads, settings and API-key metadata. Redis is the
default and holds live budgets, sessions and captured content. Temporal's PostgreSQL
is separate. Run the storage cutover and recovery checks for each deployment.

## Milestone 13: trace automated work and export team spend (delivered in 0.9.0)

- Connect configured cron/webhook triggers to retained run history, status and receipts
  in the console. Record provenance at launch, with project-scoped cursor paging and
  matching filters in both SDKs and the CLI, including JSON Lines run export.
- Download current UTC month usage as CSV from Costs, the API, both SDKs and CLI.
  Include team/project totals and provider/workflow breakdowns with explicit periods,
  reservation accounting, spreadsheet-safe names and existing role boundaries.
- Build on the existing Redis/PostgreSQL run stores, retention and Helm values.

## Milestone 14: company group access for teams (delivered in 0.9.0)

- Resolve verified OIDC groups to a single team-scoped role, with explicit denial
  for missing, malformed, unmapped or conflicting memberships. Preserve the
  existing team/project boundaries and role-claim mode when mappings are empty.
- End group-based sessions at ID-token expiry and require sign-in after affected
  policy changes, including upgrades from sessions created before group mapping.
- Inspect the current team's sign-in policy in Members & keys, the typed admin
  API and both SDKs; configure it through validated environment/Helm values.
- Cover signed-token login, authorization, session lifecycle, console flows, SDK
  calls and Helm rendering.

Group access fits the existing OIDC, role and session model. Your identity provider
manages group membership, and group updates apply at the next sign-in, bounded by token expiry.

## Milestone 15: shared approval decisions (delivered in 0.9.0)

Teams require more than one verified review before consequential workflow steps.
Multi-approver policies with configurable expiry build on the durable approval gate,
settings store and role model.

- Snapshot approval requirements, role, quorum and expiry when a run starts.
- Count 1–10 distinct reviewers; reject duplicate or late votes; any rejection ends review.
- Enforce the same durable deadline (60 seconds–7 days) in Python and TypeScript.
- Edit policies in Team settings or Helm; show count/deadline and partial reviews in
  the console. Version the worker gate API and preserve old workflow replay behavior.
- Cover policy validation, role boundaries, retries, expiry, rejection, SDK compatibility
  and console flows.

## Milestone 16: self-service workflows and run insights (delivered in 0.9.0)

- A versioned template gallery with nine workflows, one-click installation and template
  provenance on new runs.
- Encrypted per-workflow secrets, OTLP metrics, team retention settings and data export.
- Register your own workflow types from the console, CLI, API and both SDKs without a
  gateway redeploy.
- Run insights per run and per workflow, invitations, alert rules, and the
  `agentworkflows check` post-install acceptance command.

See [CHANGELOG.md](CHANGELOG.md) for the full list.

## Next

- Approval escalation on top of multi-reviewer policies.
- Broader TypeScript SDK parity with the Python framework adapters and container runner.
- Live OpenTelemetry and live-provider acceptance runs.
- Deployment guides for identity, networking, storage failover and retention, with
  recovery drills on real data.
- Broader instrumented integrations and measurable receipt coverage.
- A hosted delivery option.

## Usability and release standard

Every capability needs sensible defaults, one obvious path, actionable errors, and
copy-pasteable examples a team lead can follow in ten minutes. Prefer removing friction
to adding options. Keep provider compatibility, upgrade checks, and current release
evidence reproducible. Dates and release numbers are set at release time.

AgentWorkflows is cloud-first. Its upstream kit continues separately as a self-hosted
project with its own research publication. See [scope](docs/scope-and-non-goals.md)
for what the product covers and [CHANGELOG.md](CHANGELOG.md) for product releases.
