# Audit log

Team admins can open **Audit log** in the console to read retained gateway receipts
directly, separate from gateway logs. The view and its APIs are for team admins.
Use an admin credential without a project restriction: this view covers the whole team.

Filter by time, actor, event type, project or run ID, then choose **Apply filters**.
Times in the form use your local timezone. Events appear newest first; **Older
events** pages through the results. Run links open the existing run detail page.
**Show JSON** expands the original, already redacted event as plain text.

**Verify chain** checks every team event in the selected time range, including
events hidden by actor, event, project or run filters. The result shows how many
events were checked and, on failure, the first break's process chain, sequence
and reason. A range boundary marks where the retained or selected range begins,
separate from chain breaks. An empty range checks zero events.

**Export JSON Lines** downloads all pages of the applied filters as
`audit-log.jsonl`, newest first, one original event per line. The page captures
an upper time bound when opened or when filters are applied, so an ongoing export
covers a fixed window. Retention continues during paging and export, so export
promptly when collecting evidence.

## Enable and retain the view

The view uses the existing budget Redis: set `SANDBOX_BUDGET_BACKEND=redis`,
configure `SANDBOX_BUDGET_REDIS_URL` and enable Redis persistence. Keep
`AUDIT_LOG_ENABLED=true`. Existing Redis-backed teams use their current services.
Until that configuration is in place, the page says **Audit log is turned off** and shows how to
enable it. Storage read failures return 503.

With `STORAGE_BACKEND=postgres`, the same APIs and verification use
[PostgreSQL gateway storage](postgresql-storage.md). PostgreSQL also persists original
events and process chain heads; the operator log format remains unchanged.

`AUDIT_VIEW_RETENTION_SECONDS` defaults to **7776000** (90 days). In Helm, use
`inference-gateway.traceability.auditViewRetentionSeconds` in the umbrella chart,
or `traceability.auditViewRetentionSeconds` in the gateway chart. Each team
also keeps up to **100,000 events** in Redis, and the earlier limit applies. Redis writes and reads trim
old entries; an inactive team's stream expires after the retention interval.
The view collects events from the moment the feature is enabled.

## API

`GET /v1/team/audit` accepts inclusive `from` and `to` Unix timestamps in seconds,
exact-match `event_type`, `actor`, `project`, `run_id`, and an opaque `cursor` from
the previous response's `next_cursor`. `limit` defaults to 50 and is capped at 200.
The response contains `enabled`, `events`, `next_cursor`, and an optional `message`.
An event entry contains the unmodified `event`, stream `id`, process `chain_id`,
process `sequence`, original `record_hash`, and team projection chain metadata.
Actor matches the event's actor, or the principal's subject/key ID.

`GET /v1/team/audit/verify?from=...&to=...` returns `enabled`, `ok`, `checked`,
`first_break` (null or `{chain_id, sequence, reason}`), and `boundaries`.
When the view is off, `ok` is null. Both APIs require a team admin and return
403 with `detail.reason: team_role_required` otherwise. Responses are always fresh.

## What verification proves

The original process-wide log and its hashes are unchanged. The view recomputes
each original event hash. Because a process chain interleaves different teams,
the view also links its stored envelopes per team and process with SHA-256,
covering the original event, process sequence and team sequence. This detects
edits, internal deletions and reordering while keeping each team's view to its own events.
Direct original links are checked when process sequences are consecutive.

Checks cover the available range, and a removed prefix appears as a range boundary.
For cross-process continuity and independently anchored evidence, verify the complete
operator logs with the existing verifier, `scripts/audit-verify.py`.
See the [audit chain runbook](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/audit-chain.md)
for full-log verification, anchoring and SIEM retention.
