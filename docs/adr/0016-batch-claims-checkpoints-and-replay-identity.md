# 0016. Batch claims have owners, progress is checkpointed, items replay as the submitter

- Status: Accepted
- Date: 2026-10-04
- Deciders: Platform maintainer
- Refines: [0011](0011-async-files-and-batch-api.md) (its decisions stand; this records how three of
  its guarantees are met)

## Context

ADR 0011 specifies that a batch is durable, safe to process with N workers, and replayed only
within its tenant's identity. A review of the worker (`src/inference-gateway/app/batch_worker.py`)
set out what each guarantee needs:

- **N workers.** When the reaper hands a stalled batch to another replica, exactly one worker
  must hold the claim and be able to acknowledge it.
- **Durability.** Worker memory should stay bounded for large input files (for example 100 MB),
  and a redelivered batch should resume near where it stopped so items are charged once.
- **Tenant identity.** Replay should act for one tenant at a time, scoped to that tenant's running
  batch, and receipts should name the person who submitted the batch.

## Decision

- **Owner tokens.** `BatchStore.claim()` returns a `Claim` carrying a random token, stored with
  the claim time (`<token>|<time>` in Redis). `heartbeat(claim)` and `ack(claim)` act only when the
  token still holds the claim; the reaper reads the time after the separator. The worker
  heartbeats between chunks and stops cleanly, leaving finalization to the new owner, once its
  token is replaced. Claims written before this change age out and are re-queued normally.
- **Streamed, checkpointed processing.** `ObjectStore.open_lines()` streams input from every
  backend. Results are written as parts of `BATCH_WORKER_PART_LINES` items under
  `<tenant>/parts/<batch>/`, and after each part the batch record stores `processed_lines` and the
  part counts. A resumed batch skips the processed lines, so a crash replays at most one part.
  Parts are written before the record moves forward, so the record always points at stored
  parts; parts are combined through a spooled temporary file at finalize and then deleted.
- **Replay bound to a running batch.** Batches record their submitter (from the audit principal).
  An API-key record with the `batch_replay` scope may assert a tenant only when `X-Batch-ID`
  names that tenant's batch and the batch is `in_progress`; the request is then treated as bound
  to that sandbox, and the receipt records `batch_id` and `on_behalf_of`. Any other use of the key
  returns `403 batch_replay_not_authorized`. A worker key without the scope keeps its existing
  behavior.

## Consequences

- Exactly one worker finishes each batch, and the replica that takes over a batch owns it alone.
- Worker memory is bounded by one part plus one chunk, independent of file size. The worker pod
  uses a writable `/tmp` sized for one assembled result file; the chart mounts one.
- The worker key acts only for tenants with running batches and leaves a receipt per item naming
  the batch and its submitter. Operators issue the worker key as a scoped record.
- Delivery is at-least-once within a part: a resume after a crash replays at most the items of
  the current part.

## Alternatives considered

- **Per-batch credentials minted for the submitter.** The gateway would mint a short-lived token
  for each batch and the worker would present it, which requires token issuance and storage and an
  identity provider behind each submitter. Binding the worker to the running batch was chosen
  because it works for API-key submitters too.
- **Per-item checkpoints.** Exact resume, with a store write per item. Parts were chosen because
  they bound the replay at a small, configurable cost.
- **Redis streams with consumer groups.** Built-in ownership and pending entries in a different
  data model. The list-based queue with tokens was chosen because it meets the same guarantees on
  the existing model.
