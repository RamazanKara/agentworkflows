# 0011. Asynchronous Files and Batch API

- Status: Accepted
- Date: 2026-07-04
- Deciders: Platform maintainer

## Context

The gateway is OpenAI-compatible and already offers a **synchronous** fan-out at
`POST /v1/batch-inference`: it runs a bounded set of chat requests concurrently and returns
every result inline, within one request timeout. The OpenAI **asynchronous** file-batch API
(`/v1/files` + `/v1/batches`) is a stateful subsystem in its own right, beyond a request handler.

The requirement is to support large, offline, bulk workloads that span many requests: submit thousands of requests as a JSONL file, have them processed asynchronously under
the same governance as live traffic, poll for status, and retrieve the results (and per-item
errors) later. That is the real OpenAI Batch API shape (`input_file_id`, `completion_window`,
`status: validating → in_progress → finalizing → completed`, `output_file_id`/`error_file_id`,
`request_counts`, cancellation).

Constraints that shape the design:

- **The gateway stays stateless and horizontally scalable.** It runs as a plain Deployment with
  all state held externally, and that property is preserved.
- **Governance must be enforced exactly once, identically to live traffic.** Every batched item
  has to pass the model allowlist, admission caps, prompt-secret policy, per-tenant budget, the
  output guardrail, tenant isolation, and the tamper-evident audit chain (ADR 0006), from a single
  copy of that policy.
- **Durable, cancellable, horizontally safe.** A submitted batch must survive a worker restart,
  be cancellable mid-run, honor its completion window, and be safe to process with N workers.
- **Minimal heavyweight dependencies.** The repo keeps a lean, hash-pinned dependency set and
  hand-rolls small primitives (e.g. the audit chain) in place of large SDKs.

## Decision

Add an OpenAI-compatible asynchronous **Files** and **Batches** API as a durable,
horizontally-safe subsystem. State is externalized so the gateway stays stateless.

**API surface (served by the gateway).**

- Files: `POST /v1/files` (multipart, `purpose=batch`), `GET /v1/files/{id}`,
  `GET /v1/files/{id}/content`, `DELETE /v1/files/{id}`, `GET /v1/files`.
- Batches: `POST /v1/batches` (`{input_file_id, endpoint, completion_window, metadata}`),
  `GET /v1/batches/{id}`, `POST /v1/batches/{id}/cancel`, `GET /v1/batches` (paginated).
- All of these are authenticated and tenant-bound exactly like the existing endpoints. The batch
  `endpoint` is restricted to the governed inference routes (`/v1/chat/completions`,
  `/v1/completions`, `/v1/embeddings`), matching OpenAI's allowed set.

**Storage.**

- **Object store (S3 / MinIO)** holds the JSONL blobs (input, output, and error files), keyed by
  tenant and file id. Redis holds only metadata and the queue.
- **Redis** holds file metadata, batch job records (status, `request_counts`, file ids,
  timestamps, `completion_window`, `metadata`), and the **durable work queue** as a reliable
  Redis list pair: a `pending` list plus a `processing` list with a claim-time hash the reaper
  reads. Redis is already a platform dependency (`budget-redis`) and this uses only single atomic
  commands (`RPOPLPUSH`, `LREM`, `LPUSH`, `HSET`/`HDEL`), which keeps the queue logic simple to
  reason about.
  AOF persistence makes the queue durable.

**Processing (`batch-processor`, a new stateless Deployment).**

- Claims a batch with an atomic `RPOPLPUSH` from the `pending` list to the `processing` list, so
  each batch is owned by exactly one worker; a reaper re-queues batches whose claim has been idle
  past a threshold (crashed-worker recovery). All worker state lives in Redis and the object
  store, so the Deployment scales horizontally and restarts freely.
- For each batch: transition `validating → in_progress`; stream the input file line by line; for
  each line **replay the request against the gateway's own governed HTTP endpoint** (e.g.
  `POST /v1/chat/completions`) carrying the batch's tenant/sandbox identity and a service
  credential, at a bounded concurrency; collect `{custom_id, response|error}` into the output and
  error JSONL; update `request_counts` in Redis; on completion upload the output/error files, set
  `output_file_id`/`error_file_id`, and transition to `completed` (or `failed`).
- **Replaying through the gateway is the core decision**: governance stays single-sourced. The
  gateway holds all policy. Allowlist, admission, prompt-secret handling, budget, guardrail, tenant
  isolation, and audit all execute in the gateway per item, identically to live traffic, so batch
  items produce the same audit receipts (extending the ADR 0006 chain) and obey the same budgets.
- **Cancellation** is a per-batch flag in Redis, checked between items; the worker finalizes
  partial output and sets `cancelled`. **Expiry**: a reaper loop sweeps a batch to `expired` when
  its `completion_window` elapses before it finishes, and also reclaims orphaned work.
- **At-least-once** delivery is made safe by idempotent output writes (deterministic object keys
  per batch) and by removing the batch from the `processing` list only after the output/error
  files are durably written; a redelivered batch is a no-op because processing checks for a
  terminal state first.

**Object-store access.** A minimal in-tree S3 client (SigV4 request signing over the existing
`httpx` + `cryptography` HMAC, path-style addressing for MinIO) behind an `ObjectStore`
abstraction, with a filesystem/in-memory implementation for tests and local runs. This keeps the
dependency set lean, matching the repo's hand-rolled-primitive philosophy (cf. ADR 0006).

**Tenancy.** The batch record binds its tenant at creation. The worker replays only within that
tenant's identity, and the gateway enforces tenant binding on every replayed request, so each batch
stays within its tenant.

## Consequences

- The feature adds backing state (an object store and a durable Redis queue) plus one worker
  Deployment. The **gateway itself stays stateless**; all batch state is external. The endpoints and
  worker are off by default and enabled per deployment.
- Governance is single-sourced: because items are replayed through the gateway, per-item audit
  receipts extend the existing chain, admission and budget apply per item, and **partial completion
  is normal** (some items land in the output file, some in the error file with the standard error
  envelope). One copy of the policy serves both paths.
- The async batch API is part of the product scope alongside the synchronous
  `/v1/batch-inference` route, which stays the right tool for small inline batches.
- Operators run an object store (MinIO locally, external S3 for customers) and a durable Redis, and
  size the `batch-processor` Deployment. This is documented for both the local and customer
  overlays, with the feature off by default.
- The `completion_window` is honored as an **expiry** bound. The batch `endpoint` set is
  chat/completions/embeddings, and results are delivered as output and error files.

**Delivery phases** (each phase independently shippable and gated by `make validate` + `make
coverage` + `make production-check`):

1. **Foundations**: settings, the `ObjectStore` abstraction + fake, Redis file/batch stores,
   config/api contracts scaffolding.
2. **Files API**: `/v1/files` endpoints over the object store + Redis metadata, size/line caps.
3. **Batches API (state)**: `/v1/batches` create/get/cancel/list, job records, enqueue to the
   queue.
4. **`batch-processor` worker**: the Deployment consumes, replays through the gateway, writes
   output/error files, updates counts/status, cancellation, expiry/reaper, crash recovery.
5. **Governance & hardening**: per-item budget/audit correctness, tenant-isolation enforcement in
   replay, backpressure, at-least-once idempotency, error taxonomy, adversarial review.
6. **Deploy, SDK, docs, evidence**: MinIO chart (local) + customer S3 overlay, worker chart with
   HPA/PDB/NetworkPolicy, umbrella wiring, SDK methods, scope/architecture/README/runbook updates,
   release-gate and evidence-pack integration.

## Alternatives considered

- **In-gateway, in-process worker.** Simplest to build: an `asyncio` background task in the
  gateway drains a queue. A separate worker was chosen because it keeps the gateway stateless,
  keeps long bulk runs off the serving pods so live traffic has its own capacity, and gives
  horizontal safety and clean restart semantics.
- **Import the governance code into the worker.** Runs items in-process in the worker against the
  policy modules directly. HTTP replay was chosen because it keeps one enforcement point and one
  copy of the gateway's app wiring for both the batch path and the live path.
- **PVC filesystem for blobs.** Simple locally. An object store was chosen because it is the
  natural multi-writer blob backend across the gateway and worker, independent of node placement
  and storage-class access modes, and customers already operate one. The filesystem backend serves
  as an `ObjectStore` implementation for tests and single-node local runs.
- **A heavyweight cloud SDK (`boto3`/`aioboto3`).** A small in-tree SigV4 client was chosen because
  it keeps the deliberately lean, hash-pinned dependency set, consistent with how the repo
  hand-rolls primitives (ADR 0006). It covers object PUT/GET/DELETE/list.
- **Postgres (or another RDBMS) for job state.** Robust and queryable. Redis was chosen because it
  is already present and covers small job records plus a durable list-based queue.
