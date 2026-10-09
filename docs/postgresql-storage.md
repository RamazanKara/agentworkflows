# PostgreSQL gateway storage

The gateway can use PostgreSQL for run metadata, start intents, step receipts,
terminal results and budget summaries, audit events and chain heads, team settings,
and managed API-key metadata. `STORAGE_BACKEND=redis` remains the default, including
the existing YAML-only behavior when budget Redis is not enabled.

Redis is still required for live budgets, sessions, memberships, captured step
content, notifications, trigger coordination and expiring worker credentials.
Temporal retains its own execution database. Use a separate gateway database;
do not run these migrations against Temporal's schema. PostgreSQL 16 is the
development chart target.

## Install

Provision a database and a role that owns its schema. The gateway creates and
migrates its `aw_*` tables at startup. Give each deployment its own database or
schema and keep `SANDBOX_BUDGET_KEY_PREFIX` consistent across replicas; it also
scopes SQL records. Configure TLS in the DSN, for example `sslmode=verify-full`
with the appropriate CA available in the gateway container.

Store the DSN in a Kubernetes Secret, without putting it in Helm values:

```bash
read -rs -p 'Gateway PostgreSQL DSN: ' PG_DSN; echo
printf '%s' "$PG_DSN" | kubectl create secret generic gateway-postgres -n aw --from-file=dsn=/dev/stdin
unset PG_DSN
helm upgrade aw deploy/charts/agentworkflows -n aw --reuse-values \
  --set inference-gateway.storage.backend=postgres \
  --set inference-gateway.storage.postgres.existingSecret.name=gateway-postgres \
  --set inference-gateway.storage.postgres.existingSecret.key=dsn --wait --timeout 15m
```

Use an image built from this change; older published gateway images do not read
these settings. With the component chart, omit the `inference-gateway.` prefix.
When using the umbrella NetworkPolicy, allow the database address and port in
`networkPolicy.externalEgress`. With the component NetworkPolicy, use its
`networkPolicy.runtimeEgress` namespace/port rules.

For disposable development, the component chart can provision a separate
single-replica PostgreSQL StatefulSet, Secret and 10 GiB PVC:

```bash
helm upgrade aw deploy/charts/agentworkflows -n aw --reuse-values \
  --set inference-gateway.storage.backend=postgres \
  --set inference-gateway.storage.postgres.existingSecret.name= \
  --set inference-gateway.storage.postgres.bundled.enabled=true --wait --timeout 15m
```

The bundled server is for development; configure external PostgreSQL for managed
backups, failover and production TLS. Its generated password is reused on upgrades.
Keep its Secret with its PVC: deleting only the Secret generates a different
password that the existing database will not accept.

## Configuration reference

| Environment variable | Default | Component Helm value |
| --- | --- | --- |
| `STORAGE_BACKEND` | `redis` | `storage.backend` (`redis` or `postgres`) |
| `STORAGE_POSTGRES_DSN` | empty | `storage.postgres.existingSecret.name` and `.key` (default key `dsn`) |
| `STORAGE_POSTGRES_POOL_SIZE` | `5` | `storage.postgres.poolSize` |
| `STORAGE_POSTGRES_TIMEOUT_SECONDS` | `3` | `storage.postgres.timeoutSeconds` |
| `RUN_RECORD_RETENTION_SECONDS` | `2592000` (30 days) | `workflowRecords.runRecordRetentionSeconds` |
| `AUDIT_VIEW_RETENTION_SECONDS` | `7776000` (90 days) | `traceability.auditViewRetentionSeconds` |

The [configuration contract](https://github.com/RamazanKara/agentworkflows/blob/main/platform/config-contracts/inference-gateway.config.json)
is the machine-readable reference. The pool opens one connection per gateway
process and grows to the configured positive maximum. Size the database connection
limit for all processes and rolling upgrades. The timeout bounds pool acquisition,
startup and SQL statements; libpq connection attempts have a minimum two-second
timeout. See [Psycopg pooling](https://www.psycopg.org/psycopg3/docs/advanced/pool.html).

`/readyz` reports `dependencies.gateway_store` and returns 503 when PostgreSQL is
unavailable. `/healthz` remains a process liveness check. SQL startup failures stop
startup. Reads, settings writes and key authentication fail closed on storage
errors; there is no fallback to Redis that could expose stale settings or keys.

## Retention and audit verification

Each gateway runs a sweep every 60 seconds. A transaction-scoped advisory lock
allows one sweep per storage scope at a time. Terminal run deadlines are fixed
from Temporal's close time, and are never extended by subsequent reads. Expiring
a run removes its metadata, snapshot and step receipts, and its start intent.
Reads hide expired rows even before the sweep. Existing Redis content retention
still applies. Settings and key records, including revocations, are not expired.

The existing workflow refresh observes terminal executions and stores their
result and final budget summary. Once saved, these remain readable even when
Temporal history expires. Live runs and operations still need Temporal and Redis.
Keep Temporal retention long enough for the gateway to observe completion; if
history disappears before observation, the gateway cannot reconstruct a result
and starts the final run-retention window when it observes that absence.

Audit rows expire by storage time using `AUDIT_VIEW_RETENTION_SECONDS`. PostgreSQL
uses the time bound, without Redis's 100,000-event cap. Retained audit JSON preserves
the original receipt hashes, per-process sequences and per-team projection hashes.
Events and their persisted head are written in one transaction. Startup receipts
link to a persisted predecessor. Concurrent replicas retain separate process chains.
The console, both SDKs, cursors and verification response shapes are unchanged.

Audit appends retain the existing best-effort behavior: if storage fails, the
operator log still receives the receipt. Advancing the in-memory projection before
the write makes an interior lost append detectable on subsequent verification.
Retained prefixes are reported as boundaries; missing tails and a complete rewrite
still require independently anchored operator logs. Use `scripts/audit-verify.py`
with complete logs, not a team-only export.

## Upgrade and rollback

An ordinary upgrade leaves Redis selected and does not copy or delete records.
Selecting PostgreSQL starts a separate record store. Use the offline import below
before cutover; backend selection alone does not import anything. Do not mix
backend selections across replicas. There is no live import or dual-write mode.

### Import existing Redis records

1. Drain active workflows. Stop **all** gateway replicas, workers and other writers,
   including background refresh processes. Back up Redis, Temporal and the gateway
   database. Keep the same Redis for live accounting, sessions and captured content.
2. Use an empty gateway PostgreSQL scope, or the partial destination from an earlier
   attempt. Set `SANDBOX_BUDGET_REDIS_URL`, `SANDBOX_BUDGET_KEY_PREFIX` and
   `STORAGE_POSTGRES_DSN` in the operator environment. Keep the existing
   `AUDIT_CHAIN_STORE_*` settings: the importer reads that persisted head too,
   including when it resides in a separate Redis or a file. DSNs are not CLI arguments.
3. From `src/inference-gateway`, initialize SQL tables without starting a gateway,
   then run preflight:

   ```bash
   python -c 'from app.settings import Settings; from app.postgres_storage import PostgresStorage; s = PostgresStorage(Settings.from_env()); s.open(); s.close()'
   python -m app.redis_import --dry-run
   ```

4. With writers still stopped, import and repeat preflight:

   ```bash
   python -m app.redis_import --apply
   python -m app.redis_import --dry-run
   ```

5. Require a final `verified` progress record and exit status zero. Inspect counts,
   settings revisions, key revocations, representative runs and audit verification.
   Switch **every** gateway to `STORAGE_BACKEND=postgres`, then restart gateways and
   workers. Keep the source backup and operator audit logs for their retention period.

The command emits JSON progress and counts, never credentials or run inputs. Dry-run
performs no Redis or SQL record writes and does not run migrations. It checks the
entire retained source, receipt hashes, team projection links, available consecutive
process links, persisted heads and destination conflicts before import. The source
is checked again before and after copying; a changing source fails the command.
Natural TTL expiration can also change a long-running preflight: rerun with writers
stopped, or rehearse against a consistent backup first. Plan operator memory for the
retained dataset, which is read into memory for preflight.

Each record is committed independently; a run and its ordered timeline commit in
one transaction. If interrupted, rerun the same command. Identical existing records
are skipped, and conflicting records stop import without overwriting them. A SQL
advisory lock serializes importers for the same scope. Do not start destination
gateways until verification finishes; the import lock does not stop application
writers. Original absolute run deadlines and audit storage timestamps are preserved,
so import does not renew retention. Managed keys retain their digests, roles, project,
expiry, revocation and last-used metadata. Settings keep their original revision.

Audit receipt JSON and projection hashes are preserved, then read back and verified
in PostgreSQL. Redis retains team projections, not a complete process-wide log:
trimmed prefixes and interleaved events missing from those projections remain
verification boundaries. Import cannot recover those events, prove missing tails,
or recreate expired history. Independently verify complete operator logs with
`scripts/audit-verify.py`. Redis has no terminal-result snapshots to import; these
are populated by the gateway from Temporal after cutover while history is available.

Migrations in `app/sql/NNN.up.sql` and `.down.sql` are versioned in
`aw_schema_version`. A database-wide advisory lock serializes startup migrations;
DDL and version updates commit together. Repeated up/down calls do nothing at the
target version. A newer or noncontiguous schema version is rejected.

Back up the gateway database alongside Redis and Temporal with writers quiesced.
Restore all three consistently before restarting gateways and workers. The existing
Redis/Temporal backup scripts do not yet include the optional gateway database;
include it explicitly with your PostgreSQL backup tooling and rehearse a restore.

Do not downgrade SQL as part of a normal image rollback. Schema version 1 down is
destructive: it drops all gateway record tables. Only in a disposable database,
with all gateway processes stopped and the DSN in the environment, run from
`src/inference-gateway`:

```bash
python -c 'import os; from psycopg_pool import ConnectionPool; from app.storage_migrations import migrate; p = ConnectionPool(os.environ["STORAGE_POSTGRES_DSN"], open=True); migrate(p, target=0); p.close()'
```

Switching back to Redis exposes its old records, including old settings and key
state. Quiesce traffic and restore/reconcile the chosen store before enabling it;
never use a backend switch as an automatic outage fallback.

## Tests

The normal gateway suite uses fakes and requires no database or testcontainers.
For real SQL, use a disposable PostgreSQL database whose role can create schemas:

```bash
export TEST_POSTGRES_DSN='postgresql://test:test@127.0.0.1:5432/gateway_test'
python -m pytest -q src/inference-gateway/tests/test_postgres_integration.py
```

Each test creates a unique schema and drops only that schema afterwards. These
tests exercise up/down/up migrations, schema version rejection, concurrent settings
updates, key revocation, scoped paging, retention cascades, audit hash round trips,
and dry-run, interrupted import, resume, repeat import and destination conflicts.
`tests/test_redis_import.py` covers source verification and import orchestration with
fakes; it does not substitute for the real SQL test.
On Windows use `$env:TEST_POSTGRES_DSN` and your Windows Python environment. The
Linux/WSL repository gates remain `make lint`, `make test-gateway`, and
`make test-scripts`; WSL is not required by the fake or SQL tests themselves.
