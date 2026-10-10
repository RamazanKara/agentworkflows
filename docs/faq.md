# FAQ

## How do I run it in production?

Governed cloud providers, Temporal workflows, and approval signals are implemented. A production deployment adds identity, secrets, ingress, transport encryption, storage, observability, backups, and current evidence. See [Production readiness](production-readiness.md) and [workflows](workflows.md).

## What does the project install?

The local Argo CD profile installs the platform services and several lab add-ons. The customer profile installs a smaller application set and connects to the operator's platform operators, observability, ingress, and backup integration. [Architecture](architecture.md) lists the difference.

## Does the quickstart work offline?

The first Compose start downloads images and Python packages. After setup, the default trial runs offline on local protocol fixtures. Real cloud routes use provider access. Optional self-hosted deployments use model downloads or preloaded artifacts.

## Do I need a GPU?

The default cloud-provider trial runs on CPU with no local model. Optional Ollama runs a small model on CPU; the customer vLLM profiles use GPU resources sized for the chosen model.

## How is in-cluster traffic protected?

The data plane uses HTTP inside the cluster, and NetworkPolicy restricts which pods can connect. Enable transport encryption where your deployment requires it. See [Security overview](security-overview.md).

## Is the agent workspace a separate kernel sandbox?

It is when the cluster supplies an isolation runtime and `sandbox.runtimeClassName` selects it. Otherwise the workspace runs in a restricted container/pod security boundary on the node's container runtime.

## How is tenant identity enforced?

A sandbox-bound key record or verified JWT tenant claim binds the gateway request to a tenant. RAG can verify its own JWT as well. Use one of these bindings for multi-tenant deployments; with a shared key, `X-Sandbox-ID` is caller-supplied input.

## Which OpenAI and Anthropic APIs does the gateway implement?

The gateway implements the routes in the checked-in [OpenAPI contract](https://github.com/RamazanKara/agentworkflows/blob/main/platform/api-contracts/inference-gateway.openapi.json). Chat completions and Anthropic Messages stream; legacy completions and Responses return synchronous responses. See [Scope](scope-and-non-goals.md).

## What do the checked-in evidence files prove?

Files named `sample-*` show report shape and gate behavior, and the non-strict gate can use them. For the current checkout, a release, or a customer cluster, generate fresh reports and use `make release-gate-strict` for a handoff.

## How does the audit chain detect tampering?

It makes edits and reordering detectable in an exported chain. Add external log retention and a trusted chain-head anchor for durability and rollback detection. Each gateway process/replica has its own chain.

## What does the `regulated-offline` profile provide?

It renders a tenant namespace that blocks external CIDR egress. Private registries, model mirrors, internal identity, and cluster-wide egress policy are configured at the cluster level. See the [restricted-egress example](regulated-offline-tenant-example.md).

## How do I upgrade or roll back?

Change the immutable `CUSTOMER_REVISION`, review the rendered changes, and let Argo CD reconcile. Roll back by returning to the prior tag. Follow the [upgrade runbook](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/upgrade.md), including its rollback guidance for stateful schema or collection changes.

## Where should I report a security issue?

Use the private process in [SECURITY.md](https://github.com/RamazanKara/agentworkflows/blob/main/SECURITY.md). Keep secrets, customer data, private prompts, and exploit details out of public issues.
