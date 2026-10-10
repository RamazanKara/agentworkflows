# 0006. Tamper-evident audit hash chain

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

The gateway emits one redacted audit event per request. For customer handoff and regulated tenants,
an auditor must be able to detect whether that event stream has been edited, reordered, truncated, or
had records inserted after the fact, independently of the process that wrote it. Plain append-only
logging leaves history open to silent rewrites by a log writer or anyone with log access. The
control must be cheap (no extra infrastructure), verifiable by independent tooling, and preserve the
existing redaction guarantees.

## Decision

Link each audit event into a per-process tamper-evident SHA-256 hash chain.

- Construction, in
  [`src/inference-gateway/app/main.py`](https://github.com/RamazanKara/agentworkflows/blob/main/src/inference-gateway/app/main.py): `h_0 =
  SHA-256("genesis")`; for each record `h_i = SHA-256(h_{i-1} || canonical(record_i))`, where
  `canonical` is `json.dumps(..., sort_keys=True, separators=(",", ":"))`. The function
  `_chain_audit_event` computes the record hash over the event before adding the chain fields, then
  stamps `prev_hash` and `record_hash` onto the event and advances the stored head
  (`state.audit_prev_hash`).
- The chain is layered over already-redacted events: audit principals are non-reversible (a key-id
  digest prefix or summarized JWT claims) and payloads are summarized into fingerprints
  (`_payload_fingerprint`: counts, roles, prompt hash), so chaining adds integrity without
  reintroducing raw prompt or credential data.
- Each record carries a chain-covered wall-clock timestamp (`ts`, Unix epoch seconds as a float),
  so WHEN an action happened is protected by the same chain as WHAT happened; timestamps added by
  the log transport sit outside the chain. The auditor reference's time-window query reads this
  field (a record without `ts` reads as `-1`). Events carry the field from v0.16.0 onward.
- Each record also carries a chain-covered `chain_id` (`HOSTNAME:process_start`, set once at
  `create_app`), identifying the per-replica chain the record belongs to. It lets the verifier
  group records into independent per-process chains and anchor each head directly in interleaved
  multi-replica logs. Events carry the field from v0.20.0 onward; for earlier events the verifier
  segments chains at genesis restarts. Each replica and each restart starts its own chain, and
  verification is per chain.
- The operator verifier in `scripts/audit-verify.py` checks the live record hashes using
  the same genesis, canonical form, and `SHA-256(prev || canonical(record))`.

## Consequences

- Any edit, insertion, deletion, or reordering of emitted records breaks the chain and is detectable
  by recomputation. The control adds one SHA-256 per request and two fields per event, which is
  effectively free, with no extra service to operate.
- Detecting a wholesale rewrite (re-chaining every record from genesis) uses an external
  commitment to the head hash: editing a record without re-chaining is caught by the internal
  consistency check, and a full re-chain is caught by an anchor mismatch against an externally
  committed head.
- As of v0.20.0 the operator tooling for both checks ships in-tree:
  [`scripts/audit-verify.py`](https://github.com/RamazanKara/agentworkflows/blob/main/scripts/audit-verify.py)
  (`make audit-verify`) reads a gateway JSONL log, deduplicates the double-logged copies, groups by
  `chain_id`, and **verifies the gateway's embedded `prev_hash`/`record_hash`**; it is
  stdlib-only so an auditor runs it offline, has a `--selftest` wired into `make validate`, and
  exits non-zero on any break.
  [`scripts/audit-anchor.py`](https://github.com/RamazanKara/agentworkflows/blob/main/scripts/audit-anchor.py)
  (`make audit-anchor`) emits the per-chain head (`{chain_id, count, last record_hash}`); a later
  `audit-verify --anchor <file>` flags a shrunk chain (rollback), a changed head (re-chain), or a
  missing chain. A CronJob example that anchors the head into a ConfigMap and the SIEM-forwarding
  procedure are documented in `runbooks/audit-chain.md`. The operator chooses where to commit and
  export the anchor.
- The chain is per gateway replica (per process); the head lives in `app.state`. Each replica keeps
  its own chain, and a process restart starts a new chain from genesis. Verification operates
  per-chain, and the operator's log shipping and anchoring provide cross-replica and long-horizon
  integrity.
- The gateway implementation and the operator verifier share one canonical form and genesis, and
  maintainers keep them in lockstep so the auditor tooling always matches.

## Alternatives considered

- **Plain append-only logging.** Simplest. The hash chain was chosen because it adds detection of
  after-the-fact edits and reordering, the exact property the audit trail needs for handoff.
- **External managed audit log / SIEM with immutability guarantees.** Strong for retention and
  cross-service correlation, and operators ship these events into one. The in-service chain was
  chosen because a few lines of SHA-256 provide the integrity property locally and let the bundled
  operator tooling verify integrity offline.
- **Merkle tree per batch.** Gives efficient inclusion proofs at scale. For a per-request,
  per-process event stream, a linear hash chain (Crosby & Wallach style, as the reference notes)
  detects the same tampering with far less complexity.
- **HMAC/keyed signing of each record.** Adds authenticity when a key is held outside the writer,
  together with key management and anchoring. The unkeyed chain plus an external head commitment was
  chosen because it keeps the control dependency-free, with anchoring in the operator's hands.
