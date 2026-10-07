# FAQ

## Is this production-ready?

AgentWorkflows 0.1.0 is for evaluation. Governed cloud providers are implemented; durable workflows, human approvals, and hosted delivery are planned. Production deployments need identity, secrets, ingress, transport encryption, storage, observability, backups, and current evidence. See [Production readiness](production-readiness.md).

## What does the project install?

The local Argo CD profile installs the platform services and several lab add-ons. The customer profile installs a smaller application set and expects the operator to provide platform operators, observability, ingress, and backup integration. [Architecture](architecture.md) lists the difference.

## Does the quickstart work offline?

The first Compose build downloads images and Python packages. After setup, the default trial uses local protocol fixtures with no external inference. Real cloud routes need provider access. Optional self-hosted deployments need model downloads or preloaded artifacts.

## Do I need a GPU?

No. The default cloud-provider trial needs no model or GPU. Optional Ollama runs a small model on CPU; the customer vLLM profiles require GPU resources sized for the chosen model.

## Is in-cluster traffic encrypted?

Not by default. The data plane uses HTTP. NetworkPolicy restricts which pods can connect but does not encrypt packets. See [Security overview](security-overview.md).

## Is the agent workspace a separate kernel sandbox?

Only when the cluster supplies an isolation runtime and `sandbox.runtimeClassName` selects it. Otherwise the workspace uses a restricted container/pod security boundary on the node's normal container runtime.

## How is tenant identity enforced?

A sandbox-bound key record or verified JWT tenant claim can bind the gateway request to a tenant. RAG can verify its own JWT as well. Without those bindings, `X-Sandbox-ID` is trusted caller input and is not a safe multi-tenant boundary under a shared key.

## Does the gateway implement the full OpenAI or Anthropic API?

No. It implements the routes in the checked-in [OpenAPI contract](https://github.com/RamazanKara/agentworkflows/blob/main/platform/api-contracts/inference-gateway.openapi.json). Chat completions and Anthropic Messages can stream; legacy completions and Responses cannot in this release. See [Scope and non-goals](scope-and-non-goals.md).

## What do the checked-in evidence files prove?

Files named `sample-*` prove report shape and gate behavior. The non-strict gate may use them. They do not prove the state of the current checkout, a release, or a customer cluster. Generate fresh reports and use `make release-gate-strict` for a handoff.

## Does the audit chain prevent log tampering?

It makes edits and reordering detectable in an exported chain. Durability and rollback detection require external log retention and a trusted chain-head anchor. Each gateway process/replica has its own chain.

## What does the `regulated-offline` profile guarantee?

It renders a tenant namespace without external CIDR egress. It does not air-gap the whole cluster or configure private registries, model mirrors, internal identity, or cluster-wide egress policy. See the [restricted-egress example](regulated-offline-tenant-example.md).

## How do I upgrade or roll back?

Change the immutable `CUSTOMER_REVISION`, review the rendered changes, and let Argo CD reconcile. Roll back by returning to the prior tag. Follow the [upgrade runbook](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/upgrade.md); stateful schema or collection changes may require a separate data rollback.

## Where should I report a security issue?

Use the private process in [SECURITY.md](https://github.com/RamazanKara/agentworkflows/blob/main/SECURITY.md). Do not put secrets, customer data, private prompts, or exploit details in a public issue.
