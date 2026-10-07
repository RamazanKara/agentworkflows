# AgentWorkflows

**The agent workflow platform for teams: cloud AI through one governed gateway, with budgets and verifiable receipts.**

AgentWorkflows helps team leads, platform engineers, and developers govern agents using
OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, and Vertex Gemini. Route calls through one
gateway, bind credentials to teams, enforce model policies and budgets, and inspect receipts.
Ollama and vLLM are optional self-hosted model backends.

**Version 0.2.0 adds team operations to durable Temporal workflows.** Teams have projects,
admin/builder/approver/viewer roles, provider keys, and shared cost limits. Start, inspect,
cancel, retry, and approve runs through one authenticated API and CLI. A step timeline
connects providers, tokens, costs, durations, and receipt IDs. This repository provides a
self-managed service, not a hosted offering. Start with the [team walkthrough](workflows.md).

## Start in ten minutes

With Docker Compose, Git, Make, Bash, Python 3.12+, and curl installed:

```bash
git clone https://github.com/RamazanKara/agentworkflows.git
cd agentworkflows
make compose-up
make compose-smoke
```

The trial uses local cloud-protocol fixtures and synthetic prices, so it needs no provider
account, model download, GPU, or Kubernetes. Open <http://127.0.0.1:8080/console> with demo
key `local-development-only` to inspect models, usage, and budgets. Stop with `make compose-down`.

Follow the [quickstart](quickstart.md) for your first request, the Python package and CLI,
real provider configuration, and actionable troubleshooting.

## Find a capability

| You want to | Start here |
| --- | --- |
| Call cloud models and configure fallback | [Client examples](client-examples.md) |
| Run durable workflows with human approvals | [Workflow walkthrough](workflows.md) |
| Set team roles, projects, provider keys, and budgets | [Team setup](workflows.md#teams-projects-and-roles), [budget controls](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/budget-controls.md) |
| Verify model and reported tool-action receipts | [Audit chain](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/audit-chain.md) |
| Add retrieval or hardened workspaces | [RAG](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/rag-service.md), [agent-sandbox](agent-sandbox-integration.md) |
| Add self-hosted models | [Model selection](model-selection.md) |
| Review current support and planned work | [Feature inventory](feature-inventory.md), [scope](scope-and-non-goals.md), [roadmap](https://github.com/RamazanKara/agentworkflows/blob/main/ROADMAP.md) |
| Deploy and operate | [Production readiness](production-readiness.md), [runbooks](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/README.md) |
| Contribute | [Developer workflow](development.md), [repository map](repository-map.md) |

## Understand the receipts

The gateway records redacted, hash-chained receipts. Verification detects edits and gaps
within retained chains. Detecting truncation, missing lifetimes, or full rewrites requires
persisted restart links and external head anchors. Tool actions need an instrumented producer;
the log cannot prove actions nobody reported. Prompts are fingerprinted, not stored in clear.

Checked-in samples demonstrate report formats, not release readiness. CI supports signing
and evidence generation; AgentWorkflows release claims require fresh artifacts.
See [evidence and validation](proof.md) and [security boundaries](threat-model.md).

## Origins

Built on [private-ai-platform-kit](https://github.com/RamazanKara/private-ai-platform-kit)
([DOI: 10.5281/zenodo.21038652](https://doi.org/10.5281/zenodo.21038652)). The kit stays a
separate self-hosted project with its own paper and DOI. AgentWorkflows starts its product
versions at 0.1.0 and preserves the upstream history and Apache-2.0 attribution.
