# Documentation

Read the [published site](https://ramazankara.github.io/agentworkflows/) or follow the same
navigation here. Existing guide and runbook URLs remain valid.

## Quickstart

[Install, scaffold, approve, and verify your first workflow](quickstart.md), with the
built-in fake or an explicit real OpenAI route. Includes Windows and Bash commands.

[TypeScript quickstart](quickstart-typescript.md): PR review with approval and support
triage on the official Temporal TypeScript SDK, using the same gateway and fakes.

## Templates

[Workflow template gallery](templates.md): PR review with approval, support triage,
weekly reports, incident summaries, and document Q&A with citations. Each has a text
walkthrough that runs against the Compose fakes, plus instructions for adapting it.

## Concepts

[Workflow essentials](concepts.md), [architecture](architecture.md),
[security](security-overview.md), [threat model](threat-model.md),
[decision guide](decision-guide.md), [scope](scope-and-non-goals.md), and [FAQ](faq.md).

## Guides

[Workflows and team setup](workflows.md),
[client examples](client-examples.md), [local evaluation and Kubernetes](local-evaluation.md),
[operator tasks](getting-started.md), [customer handoff](customer-handoff-example.md),
[developer workflow](development.md), and [runbooks](../runbooks/README.md).

## Reference

[CLI and Python SDK](sdk-reference.md), [model selection](model-selection.md),
[feature inventory](feature-inventory.md), [repository map](repository-map.md),
[Helm charts](../deploy/charts/README.md), [OpenAPI](../platform/api-contracts/inference-gateway.openapi.json),
[production readiness](production-readiness.md), [evidence](proof.md),
[version matrix](version-matrix.md), and [architecture decisions](adr/README.md).

Runbooks stay at the repository root because alerts link to them; the docs build mirrors
them into the site. Repository policies are in [Contributing](../CONTRIBUTING.md),
[Security](../SECURITY.md), and [Governance](../GOVERNANCE.md).
