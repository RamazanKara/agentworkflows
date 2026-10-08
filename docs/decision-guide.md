# Decision guide

AgentWorkflows is for teams that want cloud model access governed by one gateway, with
team budgets and receipts they can verify. Self-hosted models and Kubernetes agent
workspaces are optional. Start with the [five-minute quickstart](quickstart.md).

## AgentWorkflows or LiteLLM plus Temporal?

Both can support a team workflow. AgentWorkflows **uses Temporal**, and its value is the
bundled application and governance integration. A team that already operates LiteLLM and
Temporal may gain more by keeping that stack and adding its own application.

| Decision | AgentWorkflows | Assemble LiteLLM + Temporal yourself |
| --- | --- | --- |
| Model gateway | Five cloud-provider families and optional Ollama/vLLM; supported endpoints vary | LiteLLM offers a broader provider surface, routing, spend tracking and budgets |
| Durable jobs and review | Temporal with packaged Python activities and one waiting approval per run | Temporal provides the execution and messaging primitives; implement your review rules |
| Team experience | CLI, console, roles, project run history, budgets and approval inbox share one API | Connect gateway identity and accounting to your workflow service and chosen UI |
| Evidence | Run/step-linked model and tool receipts with a hash-chain verifier | Decide how to correlate model requests, tool actions and Temporal history in your application |
| Automatic work | Configured Temporal schedules, signed webhooks and approval/failure/budget alerts | Wire triggers and notification delivery into your own service |
| Getting started | Five editable templates, fake tools and a Compose walkthrough | More assembly work, with control over every integration and user flow |
| Operations and limits | Young self-managed project; narrower APIs; production hardening remains your responsibility | You maintain the integration and upgrades across components; existing expertise may make this easier |

The LiteLLM column is based on its [gateway documentation](https://docs.litellm.ai/docs/simple_proxy);
Temporal's [message-passing guide](https://docs.temporal.io/develop/python/workflows/message-passing)
explains queries, signals and updates, including approval patterns. The integration effort
comparison is a design judgment, not a benchmark or a claim that these components lack
governance. Compare [implemented features](feature-inventory.md) against your actual requirements.

## Is 0.5.1 a fit?

| Your need | Current support |
| --- | --- |
| Govern OpenAI, Anthropic, Azure OpenAI, Bedrock, or Vertex Gemini | Milestone 1 supplies the adapters, model policies, and classification-aware fallback |
| Attribute usage and enforce team token budgets | Bind gateway credentials to a sandbox/team; inspect usage and configured-price cost estimates |
| Verify recorded model and tool actions | Hash-chained model receipts and reported tool actions; configure producers, retention, and external anchors |
| Resume durable workflows or wait for human approval | [Temporal workflow SDK](workflows.md), approval signals, per-run budgets, and crash-recovery smoke |
| Use private local models | Optional Ollama and vLLM backends |
| Buy a managed service with billing and support | This repository does not yet provide one |

Neither path makes arbitrary external side effects exactly-once: integrations must honor
idempotency keys and handle ambiguous failures. AgentWorkflows' trial uses synthetic tools
and model replies; a successful run does not prove answer quality or real-provider acceptance.
Use the [feature inventory](feature-inventory.md) for implemented behavior and the
[roadmap](https://github.com/RamazanKara/agentworkflows/blob/main/ROADMAP.md) for planned work.

## Before serving a team

Choose approved provider models, regions, credentials, and prices. Bind each team's
gateway key to its budget and classification policy. Decide which tools emit receipts
and where logs and anchors are retained. Confirm actual provider access and gather
fresh evidence; the local protocol fixtures and sample reports do not establish it.

Kubernetes deployments additionally need an owner for identity, secrets, ingress, storage,
observability, backups, and on-call work. The [production readiness matrix](production-readiness.md)
and [customer guide](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/clusters/customer/README.md)
describe those responsibilities. Bundled stateful services are evaluation footprints,
not a promise of production availability.
