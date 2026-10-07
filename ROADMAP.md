# AgentWorkflows roadmap

AgentWorkflows is the agent workflow platform for teams: governed cloud providers, durable
agent workflows, human approvals, team budgets, and receipts for model and tool calls.
This is a product direction, not a claim that every capability is available in 0.1.0.

## Milestone 1: governed cloud providers — delivered

OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, and Vertex Gemini share authentication,
model policies, classification-aware fallback, token budgets, usage accounting, and audit
receipts. Ollama and vLLM remain optional self-hosted backends. The local walkthrough
uses provider protocol fixtures; live provider acceptance is deployment-specific.

## Next: durable agent workflows

- Persist workflow state and resume after worker restarts.
- Define retry, idempotency, cancellation, and timeout behavior for model and tool steps.
- Correlate model and tool receipts with a workflow run and its steps.

The existing Batch API and stored Responses are not a durable workflow engine.

## Then: approvals and team operations

- Pause and resume runs for human approval, with attributable decisions.
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
