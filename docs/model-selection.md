# Model selection and updates

## Cloud routes (Milestone 1)

The gateway supports `openai`, `anthropic`, `azure-openai`, `bedrock`, and `vertex`
alongside `ollama` and `vllm`. Cloud templates in the existing model catalog are
**proposed**, with placeholder model IDs, limits, and prices. External traffic starts once
a route is promoted. Review provider terms, region, retention, model limits, and input/output
prices, then use the existing promotion workflow. `MODEL_ROUTING_POLICY_PATH` accepts
a `ModelCatalog` (only `status: approved` entries) or the existing `ModelRoutingPolicy`.
The catalog checker carries `connection`, `pricing`, and routing fields into the
approved environment's `routing.policy.models`.

An operator's Helm/GitOps values can contain:

```yaml
runtime:
  modelId: approved-cloud
  allowedModels: [approved-cloud]
providerCredentials:
  - env: OPENAI_API_KEY
    secretName: model-provider-credentials
    secretKey: openai-api-key
routing:
  policy:
    enabled: true
    models:
      - id: approved-cloud
        backend: openai
        connection:
          baseUrl: https://api.openai.com/v1
          model: YOUR_APPROVED_MODEL_ID
          credentialEnv: OPENAI_API_KEY
        pricing:
          inputUsdPer1kTokens: 0 # replace with your contracted rate
          outputUsdPer1kTokens: 0 # replace with your contracted rate
```

Create the referenced Secret using your existing secret backend; keep credential
values out of Git, catalog entries, Helm values, and client requests. Each connection reads
only its named environment variable. Credential-bearing URLs and inline credential
fields are rejected. Use HTTPS for real providers. The gateway's default NetworkPolicy
keeps external egress closed: provision an approved HTTPS egress policy for the
gateway namespace through the existing egress catalog, or route through an internal
egress proxy permitted by `networkPolicy.runtimeEgress`. Workspaces keep their
default-deny boundary, and provider credentials stay on the gateway.

| Backend | `connection.baseUrl` | `connection.model` / credential environment |
| --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | Approved model ID / `OPENAI_API_KEY` |
| `anthropic` | `https://api.anthropic.com/v1` | Approved Claude model ID / `ANTHROPIC_API_KEY` |
| `azure-openai` | `https://RESOURCE.openai.azure.com/openai/v1` | Azure deployment name / `AZURE_OPENAI_API_KEY` |
| `bedrock` | `https://bedrock-runtime.REGION.amazonaws.com` | Bedrock model ID or inference profile / `AWS_BEARER_TOKEN_BEDROCK` |
| `vertex` | `https://REGION-aiplatform.googleapis.com/v1/projects/PROJECT/locations/REGION/endpoints/openapi` | `google/APPROVED_GEMINI_MODEL` / `VERTEX_ACCESS_TOKEN` |

These adapters use [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create),
[Azure's v1 API](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/switching-endpoints),
[Bedrock Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
with a [Bedrock bearer API key](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html),
and [Vertex's OpenAI-compatible API](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/samples/generativeaionvertexai-gemini-chat-completions-non-streaming).
Vertex uses an OAuth access token; the operator refreshes it and rolls the gateway
when an environment-backed Secret changes. Bedrock uses the bearer API key. Readiness
checks cloud credential presence; actual calls use the shared circuit breaker and
retry policy.

All five support chat, function tools, and streaming through Chat and Messages;
Responses and synchronous batch items are non-streaming. Anthropic and Bedrock
adapters accept text and function tools; other modalities/parameters receive an
explicit, receipted 400. OpenAI and Azure also proxy embeddings and legacy completions;
those two endpoints are specific to them. Each model's own API rules apply. The provider
tests and Compose walkthrough run without live cloud credentials or paid calls.

`fallbacks` is an ordered preference: put a local route first for local preference,
or a cloud route first for cloud preference. Chat, Messages, Responses, and synchronous
batch items try the next permitted route on upstream overload (429/5xx), connection
failure, or an open circuit. Ordinary 4xx errors return directly; streaming fallback
applies until output begins. Gateway-wide load shedding returns 503 before routing.
Embeddings and legacy completions use a single route.

Set `data_classification: confidential` in an inference body, or send
`X-Data-Classification: confidential`. Tenant `dataClassification` is a floor: a body or
header can raise the classification, and the floor always applies. Values are `public`, `internal`, `confidential`, and
`restricted`; the last two permit **only local routes**, including fallback, canary,
shadow, and cache selection. If no eligible local route can serve the request, the
gateway returns `403 data_classification_denied` with a hash-chained refusal receipt.
Stored Responses and uploaded batch files/jobs preserve the floor during chaining and
replay. Bind tenant identity to a key record or verified JWT claim for this to be a
tenant security boundary.

Model metadata was reviewed against the publishers' repositories on **2026-09-23**
for the v0.2.0 release. The catalog separates models approved for the existing lab
profiles from newer candidates under evaluation.

## Models used by the shipped profiles

| Purpose | Model | Release behavior |
| --- | --- | --- |
| Local CPU smoke tests | `qwen2.5:0.5b` | Retains the small, non-reasoning model used by the quickstart. Its Ollama weight-layer digest was reverified. |
| Customer CPU lab | `qwen3.5:0.8b` | Retains the customer reasoning model. Its Ollama weight-layer digest was reverified. |
| Customer coding agents | `Qwen/Qwen3-Coder-Next` | Now pins the upstream commit and all safetensors shard checksums in a reproducible inventory. |
| Customer RAG embeddings | `BAAI/bge-small-en-v1.5` | Now pins the upstream commit and weight inventory; the embedding deployment uses the same revision. |

These approvals describe lab profiles. Read the [model cards](https://github.com/RamazanKara/agentworkflows/blob/main/platform/model-catalog/model-cards/README.md)
for each model's evidence.

## Current GPU candidates

All rows below have `status: proposed` and enter the gateway allowlists through
promotion. Context sizes are upstream configuration values. Candidate licenses and revision links are recorded in
[`platform/model-catalog/models.yaml`](https://github.com/RamazanKara/agentworkflows/blob/main/platform/model-catalog/models.yaml).

| Candidate | Upstream license | Context tokens | Evaluation focus |
| --- | --- | --- | --- |
| [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) | Apache-2.0 | 262,144 | Dense model for coding and agent workloads; first candidate to compare with the current coding profile. |
| [Qwen3.8-Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) | Qwen Community 1.0 | 262,144 | Larger architecture with custom license terms and model-specific serving requirements. |
| [GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash) | MIT | 1,048,576 | Large multi-GPU candidate; validate the publisher's serving recipe and hardware requirements. |
| [DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) | MIT | 1,048,576 | Large model with a specialized architecture; validate runtime support before capacity testing. |
| [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) | Apache-2.0 | 262,144 | Retained as a comparison candidate with verified upstream context metadata. |

Qwen3.8-Flash-Next uses the
[Qwen Community 1.0 license](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/de4b8e4d43b917e7706784d8bb445c9af86a3540/LICENSE).
The catalog records `LicenseRef-Qwen-Community-1.0` to keep its terms distinct from
the permissive license of Qwen3.8-27B.

To evaluate a candidate, use the upstream recipe, then run AgentWorkflows' real-model evals and load tests before
changing a serving profile or approving a model.

## Reproduce the approved model metadata

```bash
make model-check
make model-provenance-check
make model-provenance-verify
```

The first two commands validate local catalog, manifest, and profile consistency.
The third contacts the Ollama registry and Hugging Face metadata API. It reproduces
the approved Ollama layer digests and Hugging Face weight inventories without
downloading model weights.

A manifest digest identifies the inventory of weight filenames, byte sizes, and
upstream SHA-256 checksums at one immutable commit. To verify bytes installed in a
customer model store, compare those files against the inventory during model-store
ingestion.

See the [provenance runbook](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/model-provenance.md)
for the manifest format and update commands.

## Self-hosted model revisions

- The default vLLM chart and approved customer profiles now set `model.revision`.
  Custom overlays that change `model.name` must also set the matching revision.
- The embedding profile explicitly overrides the coding-model pin with the BGE
  revision. Keep these revisions distinct.
- The AWQ example is an operator template. It names an explicit customer
  checkpoint placeholder. Supply an approved checkpoint and revision before using it.
- Candidates are deployed and promoted through review. Follow the
  [model governance runbook](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/model-governance.md)
  to collect evaluation, load, and security evidence for a promotion.
