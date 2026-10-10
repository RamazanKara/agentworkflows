# Cost model

This page lists the inputs for a deployment cost estimate and explains how the built-in usage estimate works.

## Cost inputs

Build the estimate from the target environment:

| Area | Inputs |
| --- | --- |
| Model serving | GPU type and count, replicas, measured tokens per second, utilization, model-load time |
| Platform compute | Gateway, RAG, Redis, Qdrant, Argo CD, policy, observability, and workspace CPU/memory |
| Storage | Model cache, Qdrant data, workspace PVCs, metrics/logs, object-store data, and backups |
| Network | Registry/model downloads, user ingress, cross-zone traffic, and backup transfer |
| Operations | Cluster, identity, secret, backup, incident, upgrade, and security-review time |

Use current quotes from the provider or hardware vendor.

## Checked-in customer defaults

The customer NVIDIA example is large: two vLLM replicas at the KEDA floor, four GPUs per replica, a 262,144-token maximum context, and a 400 GiB shared model cache. These values live in `deploy/clusters/customer/values/vllm-nvidia.yaml` and are test fixtures for a coding-model profile.

Before pricing the deployment, choose the model and reduce or increase the GPU count, tensor parallelism, context length, replica floor, and storage from measured requirements. The [capacity worksheet](capacity-sizing.md) lists the relevant settings.

## Gateway usage estimate

`GET /v1/usage` reports the sandbox's settled token counters and a `providers` breakdown
of measured input/output/total tokens, requests, and estimated cost in the budget window.
The same memory or Redis budget store holds these counters. Receipts record the selected
provider, measured usage, applied prices, and `estimated_cost_usd`; Prometheus exports
cost by sandbox and provider. Cache hits add no usage or cost. Shadow calls are separately
reserved, settled, and receipted.

Cloud catalog entries require explicit `pricing.inputUsdPer1kTokens` and
`pricing.outputUsdPer1kTokens`. Rates are operator-supplied USD prices; the catalog
ships templates with zero placeholders for you to fill in:

```text
estimated_cost_usd = (prompt_tokens * input_rate + completion_tokens * output_rate) / 1000
```

Unpriced local routes retain `USD_PER_1K_TOKENS` (default `0.0`). When measured usage
exists, `/v1/usage` sums recorded costs instead of multiplying mixed-provider tokens
by one rate. Otherwise the legacy estimate remains available. Token reservations stay
conservative when a runtime omits usage. Accounting-store failures appear as
`usage_accounting: backend_unavailable` in receipts so exported receipts can reconcile
missing counters.

Anthropic cache read/write tokens are counted as input at the configured input rate.
Configure contracted rates and reconcile the estimate against provider billing.
The Compose fake prices are synthetic.

## Budgets and chargeback labels

Sandbox budgets limit requests, prompt characters, and estimated tokens. They bound accepted work according to gateway accounting. Include retries, idle capacity, model loading, storage, and platform overhead in the infrastructure estimate.

The local Argo CD profile installs OpenCost and applies owner/cost-center labels. In the customer profile, connect the labels to the customer's existing cost system for infrastructure allocation.

## Build an estimate

1. Measure the intended model on the intended GPU with the real context and concurrency mix.
2. Set a replica floor that meets baseline traffic and a ceiling the budget can tolerate.
3. Add platform CPU/memory and all persistent data, including backups and retention growth.
4. Add operator time and support obligations.
5. Divide the measured monthly cost by measured successful tokens if an internal per-token rate is useful.
6. Configure that rate in the gateway and compare its estimates with infrastructure invoices over time.

`make loadtest-local` uses a mock runtime to exercise gateway and report behavior. To measure a real runtime, point `make loadtest` at the deployed gateway and collect GPU, queue, latency, and error metrics at the same time.

See the [GPU capacity runbook](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/gpu-capacity.md), [budget controls](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/budget-controls.md), and [quota/chargeback runbook](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/quota-chargeback.md).
