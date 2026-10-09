# AgentWorkflows

**Run team AI workflows with human approval, budgets, and a traceable record of each step.**

AgentWorkflows is self-managed: Python and TypeScript workflow SDKs, a model gateway and a team web console.
Temporal keeps runs durable; the gateway controls model and tool access and records usage
and receipts. Start, inspect, approve, cancel and retry runs through the console or CLI.
Cron schedules and signed webhooks can start work, with notifications for waiting approvals,
failures and budget thresholds. People sign in with their company account (OIDC) or an API key,
and admins add members and manage keys in the console.

The **v0.7.0 candidate (unreleased)** connects triggers to retained run history and adds
CSV usage exports for team reporting. Use the [Quickstart's source-build path](quickstart.md)
to try these features; published image defaults remain on v0.5.1.

It is for **developers** turning agent scripts into repeatable jobs, **team leads** reviewing
drafts and spend, and **platform engineers** managing shared provider access. Built-in routes
cover OpenAI, Anthropic, Azure OpenAI, AWS Bedrock and Vertex Gemini; Ollama and vLLM are optional.
You still own the workflows, integrations and deployment. There is no hosted service here.

## Five-minute quickstart

With Git, Python 3.12+ and Docker Compose installed, follow the **[Quickstart](quickstart.md)**:
start the fake stack, run `agentworkflows init my-research`, start the workflow, review its
draft, approve it and verify the receipts. Bash and Windows PowerShell commands are included.
Initial image and package downloads may take longer than five minutes.

The default trial makes no external model calls and needs no cloud credentials, GPU or
Kubernetes. Its canned outputs demonstrate execution and governance, not model quality.
The [template gallery](templates.md) makes the next afternoon concrete:

| Start with | You get |
| --- | --- |
| [PR review](templates.md#code-review) | A diff review that waits for a human decision |
| [Support triage](templates.md#support-triage) | Routing suggestions and a reply draft |
| [Weekly report](templates.md#weekly-report) | One briefing from changes, support and incidents |
| [Incident summary](templates.md#incident-summary) | A timeline grounded in log excerpts |
| [Document Q&A](templates.md#document-qa) | An answer with source links and checked citation IDs |

Each walkthrough includes the input, commands, expected result and the integration work
needed for your team. Read [Concepts](concepts.md) for the model, [Guides](workflows.md) for
team setup, and [Reference](sdk-reference.md) for exact commands and defaults.

## Why use this instead of LiteLLM plus Temporal?

That is a reasonable stack to build yourself. [LiteLLM](https://docs.litellm.ai/docs/simple_proxy)
already provides broad model routing, budgets and spend tracking;
[Temporal](https://docs.temporal.io/develop/python/workflows/message-passing) supplies workflow
state and messages for review flows. AgentWorkflows itself uses Temporal.

AgentWorkflows packages a particular integration: team roles, governed workflow activities,
per-run budgets, an approval console, triggers, notifications and linked receipts. Choose it
if that shared workflow fits your team and saves integration work. Choose your own assembly
if you need broader provider support, already operate those systems, or want full control
over the application UX. This young project has a narrower API surface, and you still run
its backups, upgrades and monitoring yourself. See the [side-by-side decision guide](decision-guide.md).

## Find a capability

| You want to | Start here |
| --- | --- |
| Call cloud models and configure fallback | [Client examples](client-examples.md) |
| Run durable workflows with human approvals | [Workflow walkthrough](workflows.md) |
| Sign in with your company account and manage members and API keys | [Company sign-in](workflows.md#company-sign-in-oidc), [members and keys](workflows.md#members-and-api-keys) |
| Set team roles, projects, provider keys, and budgets | [Team setup](workflows.md#teams-projects-and-roles), [budget controls](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/budget-controls.md) |
| Verify model and reported tool-action receipts | [Audit chain](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/audit-chain.md) |
| Add retrieval or hardened workspaces | [RAG](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/rag-service.md), [agent-sandbox](agent-sandbox-integration.md) |
| Add self-hosted models | [Model selection](model-selection.md) |
| Review current support and planned work | [Feature inventory](feature-inventory.md), [scope](scope-and-non-goals.md), [roadmap](https://github.com/RamazanKara/agentworkflows/blob/main/ROADMAP.md) |
| Deploy and operate | [Install on Kubernetes](install-kubernetes.md), [Kubernetes production checklist](kubernetes-production-checklist.md), [production readiness](production-readiness.md), [runbooks](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/README.md) |
| Contribute | [Developer workflow](development.md), [repository map](repository-map.md) |

## Understand the receipts

The gateway records redacted, hash-chained receipts. Verification detects edits and gaps
within retained chains. Detecting truncation, missing lifetimes, or full rewrites requires
persisted restart links and external head anchors. Tool actions need an instrumented producer;
the log cannot prove actions nobody reported. Receipts fingerprint prompts; workflow inputs
and activity results remain in Temporal history.

Checked-in samples demonstrate report formats, not release readiness. CI supports signing
and evidence generation; AgentWorkflows release claims require fresh artifacts.
See [evidence and validation](proof.md) and [security boundaries](threat-model.md).

AgentWorkflows is Apache-2.0 licensed. See
[NOTICE](https://github.com/RamazanKara/agentworkflows/blob/main/NOTICE) for attribution.
