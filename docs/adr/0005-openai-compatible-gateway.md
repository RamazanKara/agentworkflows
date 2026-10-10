# 0005. A thin self-built OpenAI-compatible gateway

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

Every request to the platform must pass through a single control point that enforces authentication,
a model allowlist, sandbox budgets, redacted and tamper-evident auditing, Prometheus metrics, and
runtime routing, all while presenting the OpenAI HTTP API so existing clients work unchanged. The platform
also wants progressive delivery (canary and shadow) and runtime failover across resolved routes, all
expressed against the platform's own governance objects (the model catalog, sandbox budgets in
Redis, the audit chain). The question is whether to adopt an existing AI gateway or build a thin one.

## Decision

Build a small, purpose-fit gateway in `src/inference-gateway/app`.

- It serves the OpenAI-compatible surface under `/v1`: chat completions, embeddings, moderations,
  batches, models, and usage (see the route handlers and OpenAPI tags in
  [`src/inference-gateway/app/main.py`](https://github.com/RamazanKara/agentworkflows/blob/main/src/inference-gateway/app/main.py)). Embeddings are
  routed through the gateway specifically so they get the same auth, allowlist, budget, and audit
  controls as chat.
- Authentication is API-key (SHA-256 digest compared with `hmac.compare_digest`) or JWT verified
  against JWKS ([`jwt_auth.py`](https://github.com/RamazanKara/agentworkflows/blob/main/src/inference-gateway/app/jwt_auth.py)); the audit principal is
  non-reversible (a key-id digest prefix or summarized JWT claims).
- Progressive delivery and resilience are first-class: weighted canary routing
  (`inference_gateway_canary_routed_total`), fire-and-forget shadow mirroring
  (`inference_gateway_shadow_requests_total`), and runtime failover across a resolved route chain
  (`RUNTIME_FALLBACKS`), all in `main.py`.
- It emits Prometheus metrics, redacted audit events with a tamper-evident hash chain
  (see [0006](0006-tamper-evident-audit-hash-chain.md)), and propagates `X-Request-ID`,
  `X-Sandbox-ID`, and W3C `traceparent` without logging raw prompt text (per the README's
  "How It Works").
- Backend routing is by `RUNTIME_BACKEND` to Ollama or vLLM
  (see [0003](0003-inference-runtime-vllm-and-ollama.md)).

## Consequences

- The gateway is governance-shaped: routing decisions are made directly against AgentWorkflows' own
  model catalog, sandbox budgets, and audit chain. New controls (a budget type, an audit field, a
  route policy) are code in one service.
- It is small enough for one maintainer to own, pinned-base containerized, and covered by the API and
  config contract snapshots (`platform/api-contracts`, `platform/config-contracts`).
- The platform maintains this code and optimizes it for governance: the gateway is the control point
  inside the Kubernetes operating model.
- The gateway's feature set is exactly what the repo shows, and new provider integrations land as
  code in this service.

## Alternatives considered

- **A general-purpose LLM proxy.** Offers broad provider/model routing and out-of-the-box per-key
  spend tracking and rate limiting. A thin self-built gateway was chosen because the platform binds
  routing and admission to its own governance objects (model catalog, Redis-backed sandbox budgets,
  the tamper-evident audit chain) as the native data model. A customer can place a general-purpose
  proxy alongside or behind this gateway.
- **An API-gateway AI plugin.** Strong general API-gateway features (auth, rate limiting, plugins).
  The platform chose to write the model-catalog allowlist, sandbox budgets, canary/shadow, and the
  audit hash chain directly in a purpose-built gateway.
- **Clients call runtimes directly.** A single gateway was chosen because one control point enforces
  auth, allowlists, budgets, and auditing uniformly, the exact controls the platform exists to
  provide.
