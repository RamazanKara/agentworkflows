# AgentWorkflows

[![CI](https://github.com/RamazanKara/agentworkflows/actions/workflows/ci.yml/badge.svg)](https://github.com/RamazanKara/agentworkflows/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-AgentWorkflows-0b7285)](https://ramazankara.github.io/agentworkflows/)
[![License](https://img.shields.io/github/license/RamazanKara/agentworkflows)](LICENSE)

**Run team AI workflows with human approval, budgets, and a traceable record of each step.**

AgentWorkflows combines Python and TypeScript workflow SDKs, a model gateway and team web console.
Developers turn agent scripts into durable Temporal jobs; team leads review drafts and
spend; platform engineers control shared model and tool access. Connect OpenAI, Anthropic,
Azure OpenAI, AWS Bedrock and Vertex Gemini through one governed API. Self-hosted Ollama
and vLLM models are optional add-ons. You operate the service and own your integrations.

Version **0.4.0** adds a TypeScript workflow SDK and a clearer console: readable run timelines,
grouped notification receipts, explained provider fallback costs, and phone-friendly layouts.
Releases now publish signed images, Helm charts and both SDKs on every version tag.

## Your first approved workflow in five minutes

Follow the [Quickstart](docs/quickstart.md): install the Python SDK, start the local fake
stack, scaffold a project, review its draft, approve it, and verify its receipts. It includes
copy-pasteable Bash and Windows PowerShell commands and an optional real OpenAI route.
You need Git, Python 3.12+, and Docker Compose; the first image downloads may take longer.

For Node.js, follow the [TypeScript quickstart](docs/quickstart-typescript.md) to run
PR review with approval and support triage against the same local fakes.

```sh
agentworkflows init my-review --template code-review
agentworkflows init my-support --template support-triage
agentworkflows init my-weekly --template weekly-report
agentworkflows init my-incident --template incident-summary
agentworkflows init my-docs --template document-qa
```

The [gallery](docs/templates.md) has a step-by-step walkthrough for each: PR review with
human approval, support triage, a weekly report from three sources, an incident summary from
logs, and document Q&A with citations. Each runs unchanged against local fakes. The original
`research` starter remains the default. Canned answers prove execution, not model quality.

Read the docs in order: [Quickstart](docs/quickstart.md), [Templates](docs/templates.md),
[Concepts](docs/concepts.md), [Guides](docs/workflows.md), and [Reference](docs/sdk-reference.md).
For the full recovery/security/browser proof, use the [Compose smoke walkthrough](docs/local-evaluation.md).

## Why not LiteLLM plus Temporal?

That can be the right choice. [LiteLLM](https://docs.litellm.ai/docs/simple_proxy) already
offers broad model routing, budgets and spend tracking; Temporal provides the durable
execution and [workflow messages](https://docs.temporal.io/develop/python/workflows/message-passing)
used for approval flows. AgentWorkflows uses Temporal and packages the team-facing integration:
governed activities, per-run budgets, an approval console, triggers, notifications and linked receipts.

Use it when that integration matches your team. Assemble the components yourself when you
need broader provider coverage, already run that stack, or need a different workflow UX.
This is a young, self-managed project with a narrower API surface and operational work still
required. The [decision guide](docs/decision-guide.md) compares both paths without claiming
that a Compose trial establishes production readiness.

## What works today

| Capability | How to use it |
| --- | --- |
| Team web console | [Console walkthrough](docs/workflows.md#web-console); start runs from forms, approve drafts, read each step's prompt and answer, and inspect receipts and costs |
| Company sign-in, members and API keys | [Company sign-in (OIDC)](docs/workflows.md#company-sign-in-oidc) with Okta, Entra or another OIDC provider; [members and keys](docs/workflows.md#members-and-api-keys) created, expired and revoked in the console or CLI |
| Governed cloud providers and ordered fallback | [Provider configuration](docs/client-examples.md); provider credentials stay on the server |
| Durable agent workflows and human approvals | [Temporal workflow SDK and walkthrough](docs/workflows.md); retry, pause, and resume with per-run token/cost budgets |
| Automatic starts and team notifications | [Triggers and notifications](docs/workflows.md#triggers-and-notifications): Temporal schedules, signed webhooks, console/CLI pause controls, and Slack/webhook/SMTP alerts |
| Framework agents and MCP tools | [Bring your agent](docs/workflows.md#bring-your-agent); point clients at the gateway and register tools once per team |
| Workflow policy and container steps | [Workflow policy](docs/workflows.md#workflow-policy) limits providers, models, tools, egress, and budget; [container agents](docs/agent-sandbox-integration.md#container-workflow-steps) use hardened workspaces |
| Team roles, projects, and spend | [Team setup](docs/workflows.md#teams-projects-and-roles); shared provider budgets, project-scoped run history, and `agentworkflows usage` |
| Team operations dashboard and alerts | [Operations](docs/workflows.md#operate-the-service); throughput, failures, waiting approvals, and spend |
| OpenAI and Anthropic API compatibility | [Client examples](docs/client-examples.md) for chat, streaming, embeddings, Messages, Files, Batch, and Responses; support varies by provider |
| Model and tool-action receipts | Workflow tool execution is governed and receipted automatically; [other producers report actions](runbooks/audit-chain.md) with `POST /v1/receipts` |
| Optional retrieval and agent workspaces | [RAG](runbooks/rag-service.md) and hardened [agent-sandbox workspaces](docs/agent-sandbox-integration.md) |
| Deployment and operational checks | Helm, Argo CD, [release verification](docs/release-verification.md), and [production readiness](docs/production-readiness.md) |

## What the evidence proves

Receipts are redacted records on per-process SHA-256 hash chains. They fingerprint prompts;
workflow inputs and activity results remain in Temporal history. The verifier detects
edits, reordering, and gaps within a retained chain.
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
  CUSTOMER_REVISION=v0.4.0 CUSTOMER_GPU_PROFILE=nvidia
```

The GPU profile applies only to the optional self-hosted deployment. AgentWorkflows 0.4.0
is ready for evaluation; this repository does not yet provide a hosted service.

| Task | Start here |
| --- | --- |
| Explore capabilities and limits | [Feature inventory](docs/feature-inventory.md), [scope](docs/scope-and-non-goals.md) |
| Understand the request path | [Architecture](docs/architecture.md), [security overview](docs/security-overview.md) |
| Operate the platform | [Runbooks](runbooks/README.md) |
| Contribute | [Developer workflow](docs/development.md), [repository map](docs/repository-map.md), [contributing](CONTRIBUTING.md) |

Documentation: <https://ramazankara.github.io/agentworkflows/>.

Issues and pull requests are welcome. Report vulnerabilities privately through
[SECURITY.md](SECURITY.md). Licensed under Apache-2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
