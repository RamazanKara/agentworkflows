# PostgreSQL gateway storage

The gateway can use PostgreSQL for run metadata, start intents, step receipts,
terminal results and budget summaries, audit events and chain heads, team settings,
and managed API-key metadata. `STORAGE_BACKEND=redis` remains the default, including
the existing YAML-only behavior when budget Redis is disabled.

Redis continues to hold live budgets, sessions, memberships, captured step
content, notifications, trigger coordination and expiring worker credentials.
Temporal retains its own execution database. Use a separate gateway database and
run these migrations only against it. PostgreSQL 16 is the development chart target.

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

Use the v0.9.0 or newer gateway image, which reads these settings. With the component chart, omit the `inference-gateway.` prefix.
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
Keep its Secret with its PVC: the existing database accepts the password stored
in that Secret.

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
errors and stay on PostgreSQL, so settings and keys always come from the current store.

## Retention and audit verification

Each gateway runs a sweep every 60 seconds. A transaction-scoped advisory lock
allows one sweep per storage scope at a time. Terminal run deadlines are fixed
from Temporal's close time and stay fixed across subsequent reads. Expiring
a run removes its metadata, snapshot and step receipts, and its start intent.
Reads hide expired rows even before the sweep. Redis content retention continues
to apply. Settings and key records, including revocations, are retained without expiry.

The existing workflow refresh observes terminal executions and stores their
result and final budget summary. Once saved, these remain readable even when
Temporal history expires. Live runs and operations use Temporal and Redis.
Keep Temporal retention long enough for the gateway to observe completion and save
the result. When history expires first, the gateway starts the final run-retention
window at that point.

Audit rows expire by storage time using `AUDIT_VIEW_RETENTION_SECONDS`. PostgreSQL
applies the time bound only; Redis also caps audit at 100,000 events. Retained audit JSON preserves
the original receipt hashes, per-process sequences and per-team projection hashes.
Events and their persisted head are written in one transaction. Startup receipts
link to a persisted predecessor. Concurrent replicas retain separate process chains.
The console, both SDKs, cursors and verification response shapes are unchanged.

Every audit receipt is written to the operator log, including when a storage write
fails. Advancing the in-memory projection before the write makes an interior lost
append detectable on subsequent verification. Retained prefixes are reported as
boundaries. Verify tails and full-chain integrity against independently anchored
operator logs with `scripts/audit-verify.py`, using complete logs rather than a
team-only export.

## Upgrade and rollback

An ordinary upgrade keeps Redis selected and leaves its records in place.
Selecting PostgreSQL starts a separate record store. Use the offline import below
before cutover to bring existing records across. Select the same backend on every
replica. Import runs offline, with writers stopped.

### Import existing Redis records

1. Drain active workflows. Stop **all** gateway replicas, workers and other writers,
   including background refresh processes. Back up Redis, Temporal and the gateway
   database. Keep the same Redis for live accounting, sessions and captured content.
2. Use an empty gateway PostgreSQL scope, or the partial destination from an earlier
   attempt. Set `SANDBOX_BUDGET_REDIS_URL`, `SANDBOX_BUDGET_KEY_PREFIX` and
   `STORAGE_POSTGRES_DSN` in the operator environment. Keep the existing
   `AUDIT_CHAIN_STORE_*` settings: the importer reads that persisted head too,
   including when it resides in a separate Redis or a file. DSNs are read from the environment.
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

The command emits JSON progress and counts; credentials and run inputs stay out of its
output. Dry-run is read-only: it skips record writes and migrations. It checks the
entire retained source, receipt hashes, team projection links, available consecutive
process links, persisted heads and destination conflicts before import. The source
is checked again before and after copying; a changing source fails the command.
Natural TTL expiration can also change a long-running preflight: rerun with writers
stopped, or rehearse against a consistent backup first. Plan operator memory for the
retained dataset, which is read into memory for preflight.

Each record is committed independently; a run and its ordered timeline commit in
one transaction. If interrupted, rerun the same command. Identical existing records
are skipped, and a conflicting record stops import and is left in place. A SQL
advisory lock serializes importers for the same scope. Start destination gateways
after verification finishes; the import lock serializes importers only. Original
absolute run deadlines and audit storage timestamps are preserved, so retention
continues from the original timestamps. Managed keys retain their digests, roles, project,
expiry, revocation and last-used metadata. Settings keep their original revision.

Audit receipt JSON and projection hashes are preserved, then read back and verified
in PostgreSQL. Redis retains team projections, so trimmed prefixes and interleaved
events outside those projections are reported as verification boundaries. Verify
complete operator logs independently with `scripts/audit-verify.py`. Terminal-result
snapshots are populated by the gateway from Temporal after cutover while history is
available.

Migrations in `app/sql/NNN.up.sql` and `.down.sql` are versioned in
`aw_schema_version`. A database-wide advisory lock serializes startup migrations;
DDL and version updates commit together. Repeated up/down calls are idempotent at the
target version. A newer or noncontiguous schema version is rejected.

Back up the gateway database alongside Redis and Temporal with writers quiesced.
Restore all three consistently before restarting gateways and workers. Back up the
gateway database with your PostgreSQL backup tooling alongside the Redis/Temporal
backup scripts, and rehearse a restore.

Keep the SQL schema in place during a normal image rollback. Schema version 1 down
drops all gateway record tables, so run it in a disposable database only, with all
gateway processes stopped and the DSN in the environment, from `src/inference-gateway`:

```bash
python -c 'import os; from psycopg_pool import ConnectionPool; from app.storage_migrations import migrate; p = ConnectionPool(os.environ["STORAGE_POSTGRES_DSN"], open=True); migrate(p, target=0); p.close()'
```

Switching back to Redis exposes its old records, including old settings and key
state. Quiesce traffic and restore/reconcile the chosen store before enabling it.
Treat a backend switch as a planned change, separate from outage handling.

## Tests

The normal gateway suite uses fakes and runs without a database or testcontainers.
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
fakes; run the real SQL test alongside it.
On Windows use `$env:TEST_POSTGRES_DSN` and your Windows Python environment; the fake
and SQL tests run there directly. The Linux/WSL repository gates remain `make lint`,
`make test-gateway`, and `make test-scripts`.
