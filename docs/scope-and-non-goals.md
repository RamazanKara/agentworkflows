# Scope

This page describes what AgentWorkflows covers and how it fits alongside the other tools
your team runs. For per-feature defaults, use the [feature inventory](feature-inventory.md).

AgentWorkflows is cloud-first. Temporal provides durable execution and approval signals;
the gateway governs model/tool activities and per-run budgets. Self-hosted models are optional.

## What it covers

The repository contains and tests:

- the inference gateway and RAG service under `src/`;
- the Temporal workflow SDK and example under `sdk/python/`, with Compose and Helm/GitOps deployment;
- Helm charts for those services, Ollama, vLLM, Redis, Qdrant, and agent workspaces;
- local and customer Argo CD application manifests;
- Kubernetes policy, tenant templates, model catalog records, eval definitions, and SLO inputs;
- API and configuration contracts;
- validation, evidence, release, and supply-chain scripts;
- operational runbooks and customer handoff documentation.

The team console at `/console` uses the authenticated workflow API. Teams use it to start
and approve runs, inspect receipts and costs, install templates, manage triggers, members
and keys, edit team settings, and read the audit log. See the [console guide](workflows.md#web-console).

## API surface

The gateway implements these protocol families:

- OpenAI-style chat completions, legacy completions, embeddings, moderations, models, Files, Batch, and Responses;
- the project-specific synchronous `/v1/batch-inference`, usage, and sandbox-budget endpoints;
- an Anthropic Messages translation endpoint, with streaming.

The generated [OpenAPI contract](https://github.com/RamazanKara/agentworkflows/blob/main/platform/api-contracts/inference-gateway.openapi.json) is the route-level reference.

- Chat completions and Anthropic Messages stream. Legacy completions and Responses return synchronous responses.
- Responses supports the synchronous request shape, function tools with multi-turn `function_call` / `function_call_output` items, and `input_image` parts. Optional stored state supports `store`, `previous_response_id`, retrieve, delete, and input-items routes.
- The asynchronous Batch implementation accepts chat completions, completions, and embeddings. `completion_window` sets the batch expiry bound.
- Translated Messages and Responses payloads carry the supported text and tool fields through the governed chat path.

## How it fits with your platform

AgentWorkflows runs on the infrastructure your team already operates. Your platform supplies:

- the Kubernetes cluster and its upgrades;
- networks, load balancers, GPU nodes, and cloud databases;
- the identity provider and secret manager;
- production ingress, certificate authority, logging service, backup destination, and incident response;
- the choice, hosting, licensing, and validation of model weights;
- data classification and regulatory decisions for each use case;
- sizing of replicas, GPU memory, context windows, storage, retention, and SLOs for each workload;
- day-to-day operation of the deployment.

AgentWorkflows connects to these through Helm values, environment variables, and Kubernetes
Secrets. The customer values are examples to review: replace their placeholders and size the
GPU defaults and stateful services for your workload.

Training, fine-tuning, audio, and image generation run in purpose-built systems alongside
the gateway. Multi-node serving uses LeaderWorkerSet or Ray, and Open WebUI is available as
an example end-user chat UI.

## Security and compliance

The repository includes security controls and compliance crosswalks. Use them together with
your deployment's configuration and evidence:

- NetworkPolicy controls which pods can connect. Enable transport encryption where your deployment requires it.
- Select a gVisor, Kata, or equivalent `RuntimeClass` with `sandbox.runtimeClassName` to give agent workspaces a separate kernel boundary.
- Anchor hash-chain heads outside the process for durable, rollback-resistant audit logs.
- Checked-in `sample-*` evidence shows report shape. Generate fresh reports for each release or deployment.

Assess a deployment against a law, standard, or internal policy using its use case,
configuration, operations, and current evidence. The [security overview](security-overview.md),
[threat model](threat-model.md), and [production readiness matrix](production-readiness.md)
support that review.
