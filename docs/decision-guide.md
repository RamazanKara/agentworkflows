# Decision guide

AgentWorkflows is for teams that want cloud model access governed by one gateway, with
team budgets and receipts they can verify. Self-hosted models and Kubernetes agent
workspaces are optional. Start with the [ten-minute trial](quickstart.md).

## Is 0.1.0 a fit?

| Your need | Current support |
| --- | --- |
| Govern OpenAI, Anthropic, Azure OpenAI, Bedrock, or Vertex Gemini | Milestone 1 supplies the adapters, model policies, and classification-aware fallback |
| Attribute usage and enforce team token budgets | Bind gateway credentials to a sandbox/team; inspect usage and configured-price cost estimates |
| Verify recorded model and tool actions | Hash-chained model receipts and reported tool actions; configure producers, retention, and external anchors |
| Resume durable workflows or wait for human approval | Planned; not available in 0.1.0 |
| Use private local models | Optional Ollama and vLLM backends |
| Buy a managed service with billing and support | This repository does not yet provide one |

The product direction combines cloud-provider routing with durable workflow execution.
It does not claim LiteLLM's provider breadth or Temporal's workflow guarantees today.
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
