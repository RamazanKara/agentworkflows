# Audit Chain Verification & SIEM Forwarding Runbook

Use this runbook to verify the gateway's tamper-evident audit hash chain, to anchor its head so
a wholesale rewrite is detectable, and to forward the audit stream to a SIEM for long-term,
independent retention.

## Verify from the console

An unrestricted team admin can use **Audit log** to filter receipts, inspect event
JSON, verify the selected time range and export matching events as JSON Lines.
See the [audit log guide](https://github.com/RamazanKara/agentworkflows/blob/main/docs/audit-log.md)
for the APIs and interpretation of verification results.

The view appends already chained events to
`<SANDBOX_BUDGET_KEY_PREFIX>:audit:<team>` Redis streams. It uses the existing
budget Redis and is active when the budget backend is Redis and audit logging is enabled.
Keep Redis persistence enabled. `AUDIT_VIEW_RETENTION_SECONDS` defaults to 90 days;
exact stream trimming caps each team at 100,000 events. Writes and reads
trim old entries, and inactive streams expire. The view collects events from the moment it is enabled.

The process log format stays the same. A separate per-team, per-process envelope
chain covers each event and its process/team sequence, so a team verifies its
projection using only its own events. Verification recomputes both hashes and checks
adjacent links. Trimmed prefixes are reported as range boundaries. For tail, lifetime
and wholesale-rewrite detection, anchor the full process logs as described below.

When a Redis write fails, the gateway logs `audit view event could not be stored`
and the original audit log continues. The next successful append shows the gap to
the view verifier. Check gateway logs and Redis health, then use full-log evidence
to investigate missing view events. Read failures return 503 instead of a
verification result. Console exports contain the original events for the selected
team and time range; for the standalone log verifier, use the full process log export.

## What the audit events are

Team setting saves and resets emit `team_settings_changed` on the gateway's
existing chain. Each event names the authenticated actor, revision and touched
fields' before/after values and sources. The event omits provider credentials.
The verifier accepts these events alongside inference and workflow receipts.

Every sandbox-bound gateway request emits one redacted audit event (`event: inference_request`;
batch calls emit `event: batch_request`). Three more event types share the same chain: an
`agent_action` receipt for what a workspace did beyond calling a model (denied egress, tool
execution, file writes; see [ADR 0014](https://github.com/RamazanKara/agentworkflows/blob/main/docs/adr/0014-agent-action-receipts.md)),
a `rag_query` retrieval receipt from the RAG service on its own chain, and a `chain_start`
record opening each chain. These events are the **tamper-evident receipts**: each
is linked into a per-process SHA-256 hash chain (see
[ADR 0006](https://github.com/RamazanKara/agentworkflows/blob/main/docs/adr/0006-tamper-evident-audit-hash-chain.md)):

- `h_0 = SHA-256("genesis")`
- `record_hash = SHA-256(prev_hash || canonical(record))`, where `canonical` is
  `json.dumps(record, sort_keys=True, separators=(",", ":"))` over the event **before** the
  `prev_hash`/`record_hash` fields are stamped on.

Each event carries a `chain_id` (`HOSTNAME:process_start:random`, hash-covered) identifying its
per-replica chain, and a chain-covered `ts`. The random suffix keeps ids unique when a pod
restarts twice within one second, so the verifier keeps each chain separate. Events are logged twice per request (once to the
audit logger `agentworkflows.audit` and once to `uvicorn.error`), so a pod-log stream (and
Loki) carries two byte-identical copies of every record. The verifier deduplicates them.

Any edit, insertion, deletion, or reordering of emitted records breaks the chain and is detected
by recomputation. Anchoring detects a *wholesale* re-chain (every record rewritten from genesis
so the embedded hashes stay self-consistent) by comparing against an externally committed head.

## Verify a log

Export the gateway pod logs (or a Loki export) to a file, then:

    make audit-verify AUDIT_LOG=/path/to/gateway-audit.log

or pipe on stdin:

    kubectl -n inference logs deploy/inference-gateway | python3 scripts/audit-verify.py -

The verifier deduplicates the double-logged copies, groups records by `chain_id` (older logs
without it are split at genesis-restart boundaries), and reports per chain: the record count and
`OK`, or the first broken position and reason (`record_hash_mismatch`, `broken_link_or_reordered`,
`prev_hash_not_genesis`). It exits non-zero on any break. A per-process chain per gateway replica
and a fresh chain after each restart are expected; verification is per chain.

Self-test (also wired into `make validate`):

    python3 scripts/audit-verify.py --selftest

## Anchor the head (detect wholesale re-chaining)

Compute and store each chain's head so a later verify can detect a rewrite/rollback:

    make audit-anchor AUDIT_LOG=/path/to/gateway-audit.log AUDIT_ANCHOR=/path/to/anchor.json

The anchor file records, per `chain_id`, `{count, head record_hash}`. Later, compare a freshly
observed log against it:

    python3 scripts/audit-verify.py /path/to/new-export.log --anchor /path/to/anchor.json

This flags a **shrunk chain** (truncation/rollback), a **changed head without growth**
(re-chain/edit), or a **missing chain**. Because only the head is committed, the anchor file is
tiny and append-safe: honest appends advance a chain's head and count and leave
previously anchored heads intact. Store the anchor externally (a ConfigMap, an object-store bucket, or a
SIEM index) so it is outside the reach of whoever could rewrite the log.

### Scheduled anchoring in-cluster

A ready-to-adapt CronJob that pulls the gateway audit stream from Loki, anchors it, and stores the
head in a ConfigMap ships as a documented example at
[`docs/examples/audit-anchor-cronjob.yaml`](https://github.com/RamazanKara/agentworkflows/blob/main/docs/examples/audit-anchor-cronjob.yaml). The log
source (Loki URL, LogQL selector, tenant header, lookback) is environment-specific, so the example
is a template: set those fields before applying. The scripts are
stdlib-only, so any `python:3-alpine` with `scripts/audit-anchor.py` and `scripts/audit-verify.py`
mounted works.

## Forward the audit stream to a SIEM

Loki is single-tenant and retention-bounded by default (see [data retention](data-retention.md)),
so forward the audit receipts to a SIEM for independent, long-term, write-once retention. Two
supported shapes:

1. **Add a second Promtail/Grafana Alloy sink.** Point an additional `clients[]` entry (Promtail)
   or a second `loki.write` / `otelcol` exporter (Alloy) at the SIEM's ingest endpoint, scoped to
   the gateway audit stream (match on the `record_hash` field or the audit logger name). This
   duplicates the stream to the SIEM without disturbing the in-cluster Loki path.
2. **Export from Loki.** Run a scheduled LogQL query
   (`{app_kubernetes_io_name="inference-gateway"} |= "record_hash"`) and forward the results to
   the SIEM (for example via a Logstash/Vector/Fluent Bit Loki source, or the CronJob pattern
   above adapted to write to the SIEM instead of a ConfigMap).

**Also export the anchor.** Ship the anchor file / ConfigMap (`audit-chain-anchor`, key
`head.json`) to the SIEM alongside the events. The events prove internal consistency; the externally
held anchor proves the events are the originals and detects a wholesale re-chain.

If Loki `auth_enabled` is turned on for multi-tenant isolation, add the `X-Scope-OrgID` tenant
header on **every** path: the Promtail push (`clients[].tenant_id`), the Grafana datasource, and
any anchor/export query, so pushes and reads authenticate. The bundled reference keeps
`auth_enabled: false` (single-tenant); see the comment in
[`deploy/observability/applications.yaml`](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/observability/applications.yaml).

## Chain continuity across restarts

A chain covers one process lifetime. Each process opens its chain with a `chain_start`
receipt naming the previous chain and the head it had reached, which links the chains into a
chain of their own. That record is hash-linked like any other, so the claim it makes about its
predecessor is covered by the chain it opens. Deleting a whole replica's chain is therefore
detectable.

Continuity keeps the head in a store that outlives the pod. Pick a store:

    # File on a mounted volume
    AUDIT_CHAIN_STORE_BACKEND=file
    AUDIT_CHAIN_STORE_PATH=/var/lib/inference-gateway/audit-chain-head.json

    # Or Redis, if the deployment already runs one
    AUDIT_CHAIN_STORE_BACKEND=redis
    AUDIT_CHAIN_STORE_KEY=inference-gateway:audit-chain-head

The default is `memory`: chains verify individually, and each `chain_start` opens a fresh
history. The RAG service has the same settings under `RAG_AUDIT_CHAIN_STORE_*`.

The head is written on an interval (`AUDIT_CHAIN_PERSIST_INTERVAL_SECONDS`, default 5) and on
graceful shutdown, which keeps head-store writes off the request path. After a hard crash the
persisted head can trail the true tail by a few records, so the verifier checks that the named
head appears anywhere in the predecessor.

`audit-verify` reports continuity alongside the per-chain result:

    python3 scripts/audit-verify.py exported-audit.log

- A predecessor that is **present but disagrees** (truncated, or rewritten so its head
  changed) is a failure.
- A predecessor that is **absent** is reported as a note, because verifying a
  window of rotated logs legitimately starts mid-history. Pass `--strict-continuity` when the
  input is meant to be a complete history and a missing predecessor should fail.

## Offline auditor path

An auditor verifies from exported files alone. Hand them the exported audit log and the anchor file;
the verifier is stdlib-only Python:

    python3 scripts/audit-verify.py exported-audit.log --anchor exported-anchor.json

A clean exit (code 0) with every chain `OK` and no anchor problems means the receipts are intact
and unchanged since the anchor was taken. The same tooling runs against the checked-in
sample (`results/sample-gateway-audit.log`) as a smoke reference.

## Related

- [Data retention](data-retention.md): retention/redaction policy for the audit logs.
- [Traceability sandbox](traceability-sandbox.md): request correlation and the sandbox trace contract.
- [Evidence pack](evidence-pack.md): customer-facing evidence generation.
