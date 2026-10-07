# AgentWorkflows roadmap

AgentWorkflows is the agent workflow platform for teams: governed cloud providers, durable
agent workflows, human approvals, team budgets, and receipts for model and tool calls.
This is a product direction, not a claim that every capability is available in 0.1.0.

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

## Then: approvals and team operations

- Connect approval decisions to verified team identities and a team-facing review experience.
- Make team onboarding, budgets, and workflow history accessible to team leads.
- Add instrumented tool producers so receipt coverage is explicit and measurable.

Current receipts cover gateway calls and reported agent actions. They cannot prove
unreported tool activity. Current team budgets use sandbox-bound credentials and token
limits; provider cost accounting does not implement a billing service.

## Usability and release standard

Every capability needs sensible defaults, one obvious path, actionable errors, and
copy-pasteable examples a team lead can follow in ten minutes. Prefer removing friction
to adding options. Keep provider compatibility, upgrade checks, and current release
evidence reproducible. Dates and future release numbers are not promised here.

AgentWorkflows is cloud-first. Its upstream kit continues separately as a self-hosted
project with its own research publication. See [scope](docs/scope-and-non-goals.md)
for current boundaries and [CHANGELOG.md](CHANGELOG.md) for product releases.
