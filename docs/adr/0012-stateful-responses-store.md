# 0012. Server-side response state for the Responses API

- Status: Accepted
- Date: 2026-07-04
- Deciders: Platform maintainer

## Context

The gateway ships `POST /v1/responses` (the OpenAI Responses API) as a **stateless** subset:
requests are translated to and from the internal chat shape and run through the same governance
path as chat. A request asking for server-side state (`store: true` or
`previous_response_id`) returns `stateful_not_supported`, and clients resend the whole history
each turn.

The requirement is to support the stateful surface: persist a response so it can be
retrieved and chained, and expose `GET`/`DELETE`/`input_items` on stored responses.

This interacts with a deliberate privacy posture. The tamper-evident audit chain (ADR 0006)
stores only counts, roles, and SHA-256 fingerprints in place of raw prompt or completion text.
Honoring `store: true` means the gateway persists **raw conversation content** (the input
and output text) server-side. The decision is therefore explicit, opt-in, and bounded.

## Decision

Add an **opt-in** server-side response store, off by default (`RESPONSES_STORE_ENABLED`).

- **Disabled (default):** `store` / `previous_response_id` return `stateful_not_supported`, and
  raw content is persisted only after an operator turns the store on.
- **Enabled:**
  - `store: true` persists the response object, the turn's input items, and the running
    conversation, keyed by `<tenant>/<response_id>`, with a retention TTL.
  - `previous_response_id` loads the prior response (tenant-scoped; `404` if absent) and
    **prepends its conversation** so the stateless runtime is shown the full history; the
    gateway reconstructs the chain itself.
  - New endpoints: `GET /v1/responses/{id}`, `DELETE /v1/responses/{id}`, and
    `GET /v1/responses/{id}/input_items`, all tenant-scoped.
- **Storage:** a `ResponseStore` protocol with a `MemoryResponseStore` (local/tests) and a
  `RedisResponseStore` (shared, TTL-bounded, on the existing budget Redis). Records are
  tenant-scoped, so each tenant reads and deletes only its own responses.
- The content store is **separate from the audit chain**. The audit chain stays redacted
  (fingerprints only); stored responses are the caller's own conversation content, retained
  only for the TTL and deletable on demand.

## Consequences

- The gateway persists raw conversation content when an operator opts in, bounded by
  off-by-default operation, per-tenant isolation, a retention TTL, an explicit `DELETE`, and a
  store that is a distinct backend from the audit log, budget, and cache. Operators handling
  regulated data keep it off (the default) or set a short TTL.
- Every stateful request runs the full chat governance path (allowlist, admission,
  prompt-secret policy, budget, output guardrail, audit) on the reconstructed conversation, so
  every control applies to chained requests.
- The store provides the stateful synchronous surface: store, retrieve, chain, and delete.
- Operators who enable it provision a Redis keyspace for stored responses (or per-replica memory
  locally).

## Alternatives considered

- **Stateless only.** Simplest. The opt-in store was chosen because agents want server-side
  state, and making it explicit and opt-in keeps the privacy trade-off in the operator's hands.
- **Store raw content in, or alongside, the audit chain.** A separate, deletable, TTL-bounded
  backend was chosen so the audit chain keeps its redaction guarantee (ADR 0006) and stays a pure
  integrity control.
- **Persist responses to the object store (like batch blobs).** Response objects are small
  structured records with a natural TTL, which Redis fits well; the object store (ADR 0011) holds
  large JSONL batch files.
- **Background (async) responses via the batch-processor pattern.** The synchronous stateful
  surface covers the common agent use (store + chain).
