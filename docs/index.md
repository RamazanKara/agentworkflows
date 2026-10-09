# Put your team's AI agents to work

**Approvals, spend limits, and an audit trail in one platform.**

AgentWorkflows turns scattered agent scripts into durable team workflows. Developers write
Python or TypeScript. Reviewers approve drafts in the console. Platform teams control model
access and budgets through one gateway. Every governed model call, tool action, and approval
has a receipt.

![A workflow draft, costs, and approval progress in the team console](assets/console-1440.png)

*1440 px console capture with consistent test data.*

## Start with one approved result

Start the local stack from this checkout:

```sh
docker compose -f deploy/compose/compose.yaml up --build -d --wait workflow-worker
```

After images build and services become ready, follow the
[60-second console walkthrough](quickstart.md#60-second-console-walkthrough): sign in,
start the sample, and approve its draft. The simulated trial needs no cloud key and makes
no external model calls. Connect a real provider to evaluate output quality.

This is **1.0.0-rc.4 (unreleased)** source. Package versions remain 0.9.0; build the candidate
instead of pulling older published images. [Verification and remaining checks](release-verification.md).

## Choose your path

| Your job | Start here | What you get |
| --- | --- | --- |
| Build agent workflows | [Templates](templates.md), [SDK reference](sdk-reference.md) | Versioned starting points, forms, governed activities, and durable approvals. |
| Lead a team | [Teams and approvals](workflows.md), [Team settings](team-settings.md) | Company sign-in, roles, review queues, and visible spend. |
| Run the platform | [Helm install](single-tenant.md), [Production checklist](kubernetes-production-checklist.md) | A cloud-first deployment with explicit storage, identity, backup, and upgrade responsibilities. |

## Replace the glue code

Agent sprawl becomes a run list. Ad hoc review becomes an approval gate with 1–10 reviewers
and a deadline. Unbounded calls become token and dollar limits. Scattered logs become a
step timeline with costs, latency, errors, and receipts. Schedules and signed webhooks
start work through the same policies as the console.

The gateway supports OpenAI, Anthropic, Azure OpenAI, Bedrock, and Vertex Gemini; optional
Ollama and vLLM routes keep local models available. Temporal provides durable execution.
AgentWorkflows supplies the team application around it. You supply business workflows and
real tool integrations and operate the deployment.

[Build-or-adopt decision guide](decision-guide.md) · [Architecture](architecture.md).
There is no hosted service in this repository.

## Know where the evidence ends

Provider keys stay on the server; roles and projects restrict access. Receipt hash chains
expose edits and internal gaps. External head anchors detect truncation or replacement.
Inputs and results also remain in Temporal history with separate retention. Configure
content capture, provider retention, and backups for your team.

[Security overview](security-overview.md) · [Threat model](threat-model.md) ·
[Data lifecycle](team-lifecycle.md) · [Verification](proof.md)

[Roadmap](https://github.com/RamazanKara/agentworkflows/blob/main/ROADMAP.md) ·
[Adoption gaps](https://github.com/RamazanKara/agentworkflows/blob/main/docs/PRODUCT-GAPS.md) ·
[Apache-2.0 license](https://github.com/RamazanKara/agentworkflows/blob/main/LICENSE).
