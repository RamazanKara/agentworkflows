# AgentWorkflows

![Cloud-first Compose trial and team web console](docs/assets/compose-demo.gif)

[![CI](https://github.com/RamazanKara/agentworkflows/actions/workflows/ci.yml/badge.svg)](https://github.com/RamazanKara/agentworkflows/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-AgentWorkflows-0b7285)](https://ramazankara.github.io/agentworkflows/)
[![License](https://img.shields.io/github/license/RamazanKara/agentworkflows)](LICENSE)

**The agent workflow platform for teams: cloud AI through one governed gateway, with budgets and verifiable receipts.**

AgentWorkflows brings provider routing and workflow governance together for team leads,
platform engineers, and developers shipping agents. Connect OpenAI, Anthropic, Azure OpenAI,
AWS Bedrock, and Vertex Gemini behind one API. Bind credentials to teams, control which
models they can call, account for usage, and inspect the audit trail. Self-hosted Ollama
and vLLM models are optional add-ons.

Version **0.2.0**, the first public release, includes governed cloud providers, durable
Temporal workflows, agent and tool integrations, team roles and budgets, and a web console.
Scaffold research, support triage, or code review; inspect, approve, and verify each run.
Start with the [workflow guide](docs/workflows.md) and [roadmap](ROADMAP.md).

## Your first approved workflow in five minutes

Follow the [Quickstart](docs/quickstart.md): install the Python SDK, start the local fake
stack, scaffold a project, review its draft, approve it, and verify its receipts. It includes
copy-pasteable Bash and Windows PowerShell commands and an optional real OpenAI route.
You need Git, Python 3.12+, and Docker Compose; the first image downloads may take longer.

```sh
agentworkflows init my-research
agentworkflows init my-support --template support-triage
agentworkflows init my-review --template code-review
```

The [template guide](docs/templates.md) shows how to run and edit each project. Workflows
use concise SDK helpers for text calls, human approval, and worker startup. Inspect the
same runs through the CLI or the bundled web console, with results, costs, and receipts.

Read the docs in order: [Quickstart](docs/quickstart.md), [Concepts](docs/concepts.md),
[Guides](docs/templates.md), and [Reference](docs/sdk-reference.md).
For the full recovery/security/browser proof, use the [Compose smoke walkthrough](docs/local-evaluation.md).

## What works today

| Capability | How to use it |
| --- | --- |
| Team web console | [Console walkthrough](docs/workflows.md#web-console); sign in, switch identities, filter runs, approve work, and inspect receipts and costs |
| Governed cloud providers and ordered fallback | [Provider configuration](docs/client-examples.md); provider credentials stay on the server |
| Durable agent workflows and human approvals | [Temporal workflow SDK and walkthrough](docs/workflows.md); retry, pause, and resume with per-run token/cost budgets |
| Framework agents and MCP tools | [Bring your agent](docs/workflows.md#bring-your-agent); point clients at the gateway and register tools once per team |
| Workflow policy and container steps | [Workflow policy](docs/workflows.md#workflow-policy) limits providers, models, tools, egress, and budget; [container agents](docs/agent-sandbox-integration.md#container-workflow-steps) use hardened workspaces |
| Team roles, projects, and spend | [Team setup](docs/workflows.md#teams-projects-and-roles); shared provider budgets, project-scoped run history, and `agentworkflows usage` |
| Team operations dashboard and alerts | [Operations](docs/workflows.md#operate-the-service); throughput, failures, waiting approvals, and spend |
| OpenAI and Anthropic API compatibility | [Client examples](docs/client-examples.md) for chat, streaming, embeddings, Messages, Files, Batch, and Responses; support varies by provider |
| Model and tool-action receipts | Workflow tool execution is governed and receipted automatically; [other producers report actions](runbooks/audit-chain.md) with `POST /v1/receipts` |
| Optional retrieval and agent workspaces | [RAG](runbooks/rag-service.md) and hardened [agent-sandbox workspaces](docs/agent-sandbox-integration.md) |
| Deployment and operational checks | Helm, Argo CD, [release verification](docs/release-verification.md), and [production readiness](docs/production-readiness.md) |

## What the evidence proves

Receipts are redacted records on per-process SHA-256 hash chains. Prompts are fingerprinted,
not stored in clear. The verifier detects edits, reordering, and gaps within a retained chain.
Persisted restart links and externally retained head anchors are needed to detect missing
lifetimes, tail truncation, or a wholesale rewrite. A tool action that was never reported
cannot be proven by the log. See the [audit runbook](runbooks/audit-chain.md).

The repository contains validation and release-signing workflows. A passing local demo is
not a production certification or proof that a release has been published and signed.
Files named `sample-*` under `results/` demonstrate report formats; strict release gates
require fresh evidence for AgentWorkflows. See [evidence and validation](docs/proof.md).

## Deploy and operate

Start with the [five-minute quickstart](docs/quickstart.md). For Kubernetes, use the
[customer deployment guide](deploy/clusters/customer/README.md) and review identity,
secrets, ingress, storage, observability, and backups before production use. The umbrella
chart is `deploy/charts/agentworkflows`; release CI is configured to publish it at
`oci://ghcr.io/ramazankara/agentworkflows/charts/agentworkflows`.

To generate a GitOps overlay for a release after it has been published:

```bash
make customer-overlay CUSTOMER_REPO_URL=https://github.com/<you>/<fork>.git \
  CUSTOMER_REVISION=v0.2.0 CUSTOMER_GPU_PROFILE=nvidia
```

The GPU profile applies only to the optional self-hosted deployment. AgentWorkflows 0.2.0
is ready for evaluation; this repository does not yet provide a hosted service.

| Task | Start here |
| --- | --- |
| Explore capabilities and limits | [Feature inventory](docs/feature-inventory.md), [scope](docs/scope-and-non-goals.md) |
| Understand the request path | [Architecture](docs/architecture.md), [security overview](docs/security-overview.md) |
| Operate the platform | [Runbooks](runbooks/README.md) |
| Contribute | [Developer workflow](docs/development.md), [repository map](docs/repository-map.md), [contributing](CONTRIBUTING.md) |

Documentation: <https://ramazankara.github.io/agentworkflows/>.

## Origins

Built on [private-ai-platform-kit](https://github.com/RamazanKara/private-ai-platform-kit)
([DOI: 10.5281/zenodo.21038652](https://doi.org/10.5281/zenodo.21038652)). The kit remains a
separate self-hosted project with its own paper and DOI. AgentWorkflows retains its Git
history and Apache-2.0 attribution, and starts its own product releases at 0.2.0.

Issues and pull requests are welcome. Report vulnerabilities privately through
[SECURITY.md](SECURITY.md). Licensed under Apache-2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
