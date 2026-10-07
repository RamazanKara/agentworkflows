# AgentWorkflows

**The agent workflow platform for teams: cloud AI through one governed gateway, with budgets and verifiable receipts.**

AgentWorkflows helps team leads, platform engineers, and developers govern agents using
OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, and Vertex Gemini. Route calls through one
gateway, bind credentials to teams, enforce model policies and budgets, and inspect receipts.
Ollama and vLLM are optional self-hosted model backends.

**AgentWorkflows v0.2.0 is the first public release.** It brings governed cloud providers, durable
Temporal workflows, agent and tool integrations, team roles and budgets, and a web console.
Scaffold research, support triage, or code review; start, inspect, cancel, retry, and approve
runs through the console or CLI. Step timelines connect providers, tokens, costs, durations,
and receipt IDs. This repository provides a
self-managed service, not a hosted offering. Start with the [team walkthrough](workflows.md).

## Your first hour

1. **[Quickstart](quickstart.md)** — in about five minutes, install, scaffold research,
   review and approve a draft, and verify receipts. Use the built-in fake or an explicit
   real OpenAI route. Windows PowerShell and Bash commands are included.
2. **[Concepts](concepts.md)** — understand teams, runs, activities, budgets, and approvals.
3. **[Guides](templates.md)** — edit research, support triage, or code review, run your
   worker, then connect [your team's agents and tools](workflows.md#bring-your-agent).
4. **[Reference](sdk-reference.md)** — find CLI commands, SDK defaults, environment
   variables, and remedies for errors.

The fake needs no cloud key, GPU, model download, or Kubernetes. Initial container/package
downloads can extend the first run. The local stack is an evaluation environment;
[team setup](workflows.md#teams-projects-and-roles) explains roles and deployment controls.

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
versions at 0.2.0 and preserves the upstream history and Apache-2.0 attribution.
