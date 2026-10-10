# Decision guide

AgentWorkflows is for teams that want cloud model access governed by one gateway, with
team budgets and receipts they can verify. Self-hosted models and Kubernetes agent
workspaces are optional. Start with the [five-minute quickstart](quickstart.md).

## AgentWorkflows or your own gateway plus Temporal?

Both can support a team workflow. AgentWorkflows **uses Temporal**, and its value is the
bundled application and governance integration. A team that already operates a model
gateway and Temporal can keep that stack and add its own application.

| Decision | AgentWorkflows | Assemble a gateway + Temporal yourself |
| --- | --- | --- |
| Model gateway | Five cloud-provider families and optional Ollama/vLLM routes behind one governed gateway | Choose a gateway for the provider surface, routing, spend tracking and budgets you want |
| Durable jobs and review | Temporal with packaged Python and TypeScript activities and durable approval gates for 1–10 reviewers | Temporal provides the execution and messaging primitives; implement your review rules |
| Team experience | CLI, console, roles, project run history, budgets and approval inbox share one API | Connect gateway identity and accounting to your workflow service and chosen UI |
| Evidence | Run/step-linked model and tool receipts with a hash-chain verifier | Decide how to correlate model requests, tool actions and Temporal history in your application |
| Automatic work | Configured Temporal schedules, signed webhooks and approval/failure/budget alerts | Wire triggers and notification delivery into your own service |
| Getting started | Nine versioned templates, fake tools and a Compose walkthrough | More assembly work, with control over every integration and user flow |
| Operations | Self-managed gateway, workers, Temporal and storage, with Helm charts and runbooks | You maintain the integration and upgrades across components; existing expertise helps |

Temporal's [message-passing guide](https://docs.temporal.io/develop/python/workflows/message-passing)
explains queries, signals and updates, including approval patterns. Compare the
[feature inventory](feature-inventory.md) against your requirements.

## Is 0.9.1 a fit?

| Your need | How AgentWorkflows covers it |
| --- | --- |
| Govern OpenAI, Anthropic, Azure OpenAI, Bedrock, or Vertex Gemini | Milestone 1 supplies the adapters, model policies, and classification-aware fallback |
| Attribute usage and enforce team token budgets | Bind gateway credentials to a sandbox/team; inspect usage and configured-price cost estimates |
| Verify recorded model and tool actions | Hash-chained model receipts and reported tool actions; configure producers, retention, and external anchors |
| Resume durable workflows or wait for human approval | [Temporal workflow SDK](workflows.md), approval signals, per-run budgets, and crash-recovery smoke |
| Use private local models | Optional Ollama and vLLM backends |

With either path, integrations honor idempotency keys and handle ambiguous failures for
external side effects. The AgentWorkflows trial uses synthetic tools and model replies;
connect a real provider to evaluate answer quality. Use the
[feature inventory](feature-inventory.md) for implemented behavior and the
[roadmap](https://github.com/RamazanKara/agentworkflows/blob/main/ROADMAP.md) for upcoming work.

## Before serving a team

Choose approved provider models, regions, credentials, and prices. Bind each team's
gateway key to its budget and classification policy. Decide which tools emit receipts
and where logs and anchors are retained. Confirm provider access and gather fresh
evidence for your deployment.

Kubernetes deployments additionally need an owner for identity, secrets, ingress, storage,
observability, backups, and on-call work. The [production readiness matrix](production-readiness.md)
and [customer guide](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/clusters/customer/README.md)
describe those responsibilities. Bundled stateful services are sized for evaluation;
connect external Redis and PostgreSQL for production.
