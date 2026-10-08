# Data Retention Runbook

Use this runbook when reviewing customer handoff evidence, audit-log handling, RAG knowledge, restore reports, or coding-agent workspace data.

## Policy

The retention policy lives in `platform/governance/data-retention.yaml`.

Default policy:

- Gateway and RAG audit logs keep hashes, lengths, IDs, timing, status, usage, and result IDs. They must not store raw prompts, completions, or RAG queries.
- Generated evidence is retained for release and audit review, with sample files committed and generated run files ignored.
- RAG knowledge and vector-store collections require review before customer use.
- Coding-agent workspace PVC data should be purged on tenant offboarding.
- Model governance evidence has longer retention because it supports model lifecycle audit.

## Workflow records and captured content

The gateway uses the existing budget Redis for these records. Configure positive integer
gateway environment variables:

| Variable | Default | Applies to |
| --- | --- | --- |
| `CONTENT_RETENTION_SECONDS` | `604800` (7 days) | Each captured run/step input/output key, from its last write |
| `CONTENT_MAX_BYTES` | `16384` (16 KB) | Each captured input or output field, in UTF-8 bytes after redaction |
| `RUN_RECORD_RETENTION_SECONDS` | `2592000` (30 days) | Terminal run metadata, budget, timeline, capture markers, notifications, and start intent/input |

`captureContent: none|redacted|full` is a team default and an optional per-workflow override
in `SandboxPolicySet`. Capture defaults to `none` when both are absent; the Compose demo
team uses `redacted`. Full capture still follows gateway admission and output DLP; redacted
capture additionally runs the existing redactors on stored text. Receipts remain fingerprints
and accounting metadata. Run readers must pass the existing authenticated team/project checks.

Content lives under `PREFIX:workflow:TEAM:RUN:content:STEP_HASH`, separately from the
timeline and receipts. Each value contains text input/output, per-field `truncated` flags,
and the `redaction` mode. After content expiry the API returns `content: null` with
`content_reason: expired`; uncaptured steps return `capture_off`.

The run monitor checks Temporal every 30 seconds and applies a fixed expiry at Temporal's
close time plus the run retention period for completed, failed, canceled, terminated,
timed-out, and continued-as-new runs. Inspection also applies retention. Reads and duplicate worker
initialization do not extend deadlines. Active runs do not get a run-record TTL.
Shorter existing TTLs, including content TTLs, are preserved. Expired run IDs are pruned
from project indexes during listing and monitoring, without breaking pagination.

An idempotent online state migration runs in the monitor after gateway startup. It scans
existing run metadata in batches and asks Temporal for each execution's state, because
older Redis metadata did not store terminal status. It backfills terminal TTLs without
resetting active runs, audit chains, budgets for other runs, or identity/session keys.
Already-old terminal records expire immediately. When Temporal has already purged a run,
the migration starts its final Redis retention window at discovery. Transient failures
leave the migration pending for retry; check gateway warnings, Redis, and Temporal health.

These TTLs do not erase Temporal history, backups, exported audit logs, or workflow side
effects. Configure Temporal namespace retention and backup expiry separately. Changing
retention settings affects new deadlines; existing earlier deadlines are never extended.

## Validate Retention

Run:

    make retention-check

Generate JSON and Markdown retention evidence:

    make retention-report

Reports are written under `results/retention/`.

## Customer Handoff

Before handoff, confirm:

- audit logs do not contain raw prompt, completion, or query text
- generated evidence is retained according to customer policy
- RAG knowledge and vector-store collections have been approved for the environment
- agent workspace PVCs have an offboarding and purge process
- model governance reports are retained with model approval evidence

## Erasing a RAG Source (Right-to-Erasure)

Ingestion is upsert-only, so removing a source from the manifest leaves its vectors in
Qdrant. To purge a source's vectors (right-to-erasure or source decommission), delete by
`source_id`:

```bash
# Purge across all collection versions:
python scripts/rag-ingest.py --delete --source-id <source-id> \
  --qdrant-url "$QDRANT_URL" --collection "$QDRANT_COLLECTION"

# Or scope the delete to one collection version:
python scripts/rag-ingest.py --delete --source-id <source-id> \
  --qdrant-url "$QDRANT_URL" --collection "$QDRANT_COLLECTION" --collection-version v2
```

This issues a filtered Qdrant `points/delete` on the `source_id` payload field written at
ingest time. To re-index a source after a content change, run `--delete --source-id <id>`
followed by `--write`. Record the deletion in the retention evidence for the environment.

## Age-Based Retention Purge

Ingested chunks carry an `ingestedAtEpoch` timestamp, so the `retentionDays` policy can be
enforced by purging points older than the retention window (run on a schedule, e.g. a CronJob):

```bash
python scripts/rag-ingest.py --purge --older-than-days 180 \
  --qdrant-url "$QDRANT_URL" --collection "$QDRANT_COLLECTION"
```

This deletes every point whose `ingestedAtEpoch` is older than the cutoff (optionally scoped
to a `--collection-version`). Set `--older-than-days` to the `retentionDays` value from
`platform/governance/data-retention.yaml` for the relevant retention class, and retain the
purge summary as retention evidence.

## Changing Retention

Tune `retentionDays` and classifications only through reviewed changes to `platform/governance/data-retention.yaml`. If a customer requires longer retention or stricter classification, update the policy first and regenerate `make retention-report`.
