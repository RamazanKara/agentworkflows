# Disaster Recovery Runbook

This runbook defines the single-cluster disaster-recovery (DR) posture for the platform: what the platform
backs up, the recovery-point and recovery-time targets those backups imply, the order in which the
platform is restored, and -- explicitly -- what the platform does *not* do and hands off to the operator.

Scope is one cluster. The deployment templates ship no secondary cluster, no multi-region
topology, and no warm standby. Everything beyond a single-cluster rebuild is operator-owned and is
listed under [Operator-Owned Scope](#operator-owned-scope).

For a confirmed data-loss or data-exposure event treat this as **SEV1** and start from the
[incident-response index](incident-response.md) first; this runbook is the recovery procedure that
the SEV1 path routes into. For backup-tooling failures (a drill or backup that did not complete) use
[restore-drill.md](restore-drill.md) instead -- a failed drill is not yet a disaster.

## What AgentWorkflows Backs Up

Two complementary mechanisms ship in `deploy/backup`:

- **Velero schedule** (`deploy/backup/velero/schedule.yaml`). A daily `Schedule` named
  `ai-platform-daily` running at `0 2 * * *` with `ttl: 168h0m0s` (7-day retention). It backs up the
  **data-bearing namespaces only** and sets `defaultVolumesToFsBackup: true` so PVC *contents* are
  captured, not just resource metadata. The included namespaces are:
  - `vector` -- Qdrant vector-store PVCs (RAG embeddings)
  - `ai-agents` -- agent-workspace PVCs
  - `argocd` -- GitOps / Argo CD application and sync state
  - `ollama` -- local model store
  - `restore-drill` -- restore-drill evidence consumed by the smoke test

  This schedule does **not** cover gateway/workflow state in `budget`, `inference` or `workflows`.
  Redis is durable application state when workflows are enabled. Optional gateway PostgreSQL,
  Temporal databases, file/object storage and encryption/signing keys need the coordinated backup
  procedure below. The stateless model runtimes can be reconstructed from images and model pulls.

- **restore-drill** (`deploy/backup/restore-drill`, runbook [restore-drill.md](restore-drill.md)).
  This *validates* recoverability; it is not itself a backup. The Kubernetes CronJob
  (`deploy/backup/restore-drill/k8s/cronjob.yaml`) runs daily at `0 3 * * *`, one hour after the
  Velero schedule. Two drills exist and prove different things: the default Redis-AOF smoke proves
  the restore *tooling* runs, while `RUNTIME=local RESTORE_DRILL_QDRANT_DATA=1 make restore-drill`
  proves real Qdrant vector data is recoverable end to end. Read
  [restore-drill.md](restore-drill.md) before relying on either as evidence.

The Velero `BackupStorageLocation` and `VolumeSnapshotLocation`, and the object-storage target
behind them, are **not** shipped by the platform -- they are operator-owned (see
[Operator-Owned Scope](#operator-owned-scope) and the customer handoff checklist in
`deploy/clusters/customer/README.md`). A metadata-only backup restores empty data stores, so CSI
volume snapshots or `defaultVolumesToFsBackup` must be in effect for the PVC-bearing namespaces.

## RPO And RTO Targets

**RPO (recovery point objective)** is bounded by backup cadence. With the shipped Velero schedule at
`0 2 * * *` and AgentWorkflows' snapshot/AOF cadence, the worst-case data-loss window for the protected
data stores is **up to 24 hours** -- the time between the last completed daily backup and the
failure. The realised loss is smaller for stores that also keep their own continuous journal (Redis
AOF, where used) but you should plan against the 24-hour bound unless you raise the schedule
frequency or add CSI snapshot intervals. These cadences are operator-tunable: shorten the Velero
`schedule` and add a `VolumeSnapshotLocation` to tighten RPO.

**RTO (recovery time objective)** is the time to a serving platform after the decision to recover. It
is dominated by data restore time (Qdrant snapshot size, agent-workspace PVC size) and runtime
warm-up (image and model re-pull -- the model weights are the long pole, especially large vLLM
models). As a single-cluster planning target, aim for **bring-up within a few hours** for a
moderate data footprint, with the caveat that very large model weights and large vector collections
extend the runtime and vector-store steps. Measure your own RTO with a real restore drill rather
than trusting this estimate; the platform gives you the drill to do exactly that.

> These are *targets for a single-cluster rebuild from good backups*. They assume the operator-owned
> backup target and Velero locations exist and are healthy. With no off-cluster backup, the cluster
> loss is unrecoverable and RPO/RTO are undefined -- see [Operator-Owned Scope](#operator-owned-scope).

## Per-Store Data-Loss Window

| Store | Namespace | Backed up by | Worst-case loss on cluster failure | Notes |
| --- | --- | --- | --- | --- |
| Qdrant vector store | `vector` | Velero daily (PVC contents) + Qdrant snapshots | Up to 24h of newly ingested vectors | Rebuildable from the source-of-truth knowledge ingestion if no good snapshot exists (re-embed). |
| Agent-workspace PVCs | `ai-agents` | Velero daily (PVC contents) | Up to 24h of uncommitted workspace state | Anything pushed to a Git remote is not lost; only un-pushed local working state is. |
| Argo CD / GitOps state | `argocd` | Velero daily; also reconstructable from Git | Effectively zero for desired state | The Git repo is the source of truth; Velero restores app/sync state faster than re-bootstrapping. |
| Ollama model store | `ollama` | Velero daily; also re-pullable | Up to 24h, but re-pullable | Models are re-pullable from the registry/model store, so this is convenience, not unique data. |
| Gateway/budget Redis | `budget` (or the installation namespace) | Coordinated RDB/AOF backup; excluded from shipped Velero schedule | Since the last consistent snapshot | Contains accounting, sessions, secrets and, with the Redis storage backend, runs, settings, keys and receipts. Never silently reset it. |
| Gateway PostgreSQL (optional) | `inference` or external | Coordinated `pg_dump -Fc` or managed-store snapshot | Since the last consistent snapshot | Gateway records and schema version; Redis accounting is still required. |
| Temporal PostgreSQL | `workflows` or external | Both `temporal` and `temporal_visibility` databases | Since the last consistent snapshot | Preserve workflow history and visibility together; independent of gateway SQL schema. |
| Inference / vLLM runtimes | `inference`, `vllm` | **Not backed up** (stateless) | None (no unique data) | Reconstructed by GitOps + image/model re-pull. |

## Whole-Platform Recovery Sequence

Recover in dependency order. Each step assumes the previous one is healthy; restoring out of order
either fails admission or resurrects an empty store. Before starting, classify the incident and page
per [incident-response.md](incident-response.md), and pause any Argo CD auto-sync you do not want
fighting the restore (see [upgrade.md](upgrade.md)).

1. **Cluster + Argo CD / GitOps state first.** Stand up the cluster (or the rebuilt one), install
   Argo CD, and restore the `argocd` namespace -- either by Velero restore or by re-bootstrapping
   from Git (`make bootstrap-argocd`, then `make sync`; for the customer overlay,
   `ENVIRONMENT=customer make bootstrap-argocd && ENVIRONMENT=customer make sync`). *Why first:*
   GitOps is the control plane that schedules everything else. The Git repo is the source of truth
   for desired state, so the platform's shape is reconstructable even if the `argocd` PVC backup is
   stale; restore it before the data stores so the workloads have somewhere to land.

2. **Vector store (Qdrant PVC) next.** Let GitOps create the `vector` workloads, then restore the
   Qdrant data: restore the `vector` namespace PVCs from Velero, or restore the collection from a
   Qdrant snapshot following the snapshot-restore path documented in
   [restore-drill.md](restore-drill.md) ("Real Data-Recovery Drill (Qdrant)") against the real
   `customer-platform-knowledge` collection. *Why before agents and runtimes:* RAG and any
   agent that retrieves context depend on the vector store being present and populated; bringing it
   up early lets later smoke tests actually exercise retrieval. After restore, confirm recovered
   point counts match expectations before declaring this store recovered.

3. **Agent-workspace PVCs next.** Restore the `ai-agents` namespace PVCs from Velero. *Why here:*
   workspaces are independent of the runtimes but should be back before users return; restoring them
   after the vector store keeps the data-restore work batched and lets you validate storage health on
   the smaller PVCs first. Only un-pushed local working state is at risk -- anything pushed to a Git
   remote is intact.

4. **Runtimes via image + model re-pull.** Let GitOps reconcile `inference` and `vllm`; these hold
   no unique data and are rebuilt entirely from image pulls and model-weight pulls. *Why after the
   data stores:* the runtimes are stateless and re-pullable, so they are deliberately last among the
   serving components -- there is nothing to "restore," only to warm up. This is typically the
   slowest step (large model weights); start the pulls as early as the cluster allows but do not
   gate the data restores on it. Confirm the model runtime has ready endpoints
   (see [incident-inference-runtime.md](incident-inference-runtime.md)).

5. **Gateway and workflow state before reopening ingress.** Restore Redis, both Temporal databases,
   optional gateway PostgreSQL, object data and the original encryption/signing keys from one
   recovery point. Keep gateway and workers stopped until the stores are verified, then start
   Temporal, gateway and workers in that order. Run the checks below before accepting new work;
   restoring only Redis or only Temporal can repeat completed effects or lose budget holds.

After all steps, run the smoke and evidence checks before declaring recovery: `make eval` (and
`make loadtest`) against the gateway, RAG retrieval smoke, and -- for the recovered vector store --
re-confirm point counts. Capture the full timeline and the restore artifacts as incident evidence
per [incident-response.md](incident-response.md).

## Operator-Owned Scope

AgentWorkflows' boundary is "manifests, charts, service code, validation tooling, and runbooks." The
following are deliberately *not* shipped and are the operator's responsibility for any real DR
posture beyond a single-cluster rebuild:

- **Off-cluster backup target.** Velero needs a `BackupStorageLocation` (object storage) and a
  `VolumeSnapshotLocation` that the operator provisions and credentials. The platform ships the schedule
  template and the data-bearing namespace list; without a configured, off-cluster target the backups
  do not survive cluster loss and the restore drill validates nothing. See the handoff checklist in
  `deploy/clusters/customer/README.md`.
- **Secondary cluster.** There is no standby cluster in the platform. Cross-cluster restore (Velero
  restore into a fresh cluster) is operator-driven.
- **Multi-region / warm standby.** Region failover, replication, and a warm-standby topology are out
  of scope for the current deployment templates and are handed off on the [roadmap](https://github.com/RamazanKara/agentworkflows/blob/main/ROADMAP.md) under
  "Remaining External / Operator-Owned Work."
- **Backup-target and snapshot scheduling decisions.** The Velero cadence and retention, CSI
  snapshot intervals, and any tightening of RPO are per-environment operational decisions; the platform
  ships sane defaults the operator tunes.

## Related Runbooks

- [Incident response index](incident-response.md) -- severity tiers and the SEV1 path that routes here.
- [Restore drill](restore-drill.md) -- backup-tooling smoke and the real Qdrant data-recovery drill.
- [Budget controls](budget-controls.md) -- budget posture while Redis recovers.
- [Inference runtime incident](incident-inference-runtime.md) -- bringing runtimes back to ready.
- [Upgrade & rollback](upgrade.md) -- pausing Argo CD automation during a controlled change.
- [Runbooks index](README.md) -- the full runbook catalog.


## Gateway and worker backup verification

For the default Redis-backed Compose installation, the existing script stops workers, gateway
and Temporal, exports both PostgreSQL databases and Redis, collects receipts, records SHA-256
checksums and an audit anchor, then restarts the source even if backup fails. Quiesce external
writers and ingress first. Protect the backup directory as sensitive data and copy it off-host;
retain its manifest and audit anchor independently so rewritten backups cannot replace the trust
anchor. The shipped daily Velero schedule does not set an RPO for these additional stores.

```sh
python3 scripts/workflow-recovery.py backup --project source-project --directory /secure/aw-backup --receipts /secure/retained-receipts.jsonl
python3 scripts/workflow-recovery.py verify --directory /secure/aw-backup
python3 scripts/workflow-recovery.py restore --project new-isolated-restore --directory /secure/aw-backup
```

Use the actual source Compose project and a new, empty restore project. `restore` refuses running
consumers or nonempty stores. `verify` is read-only and runs natively without Docker: it rejects
missing/empty files, checksum mismatches and broken receipt chains. It does **not** prove that a
database can be restored. The default helper covers Redis-backed gateway records and Temporal;
it does not back up optional gateway PostgreSQL, external stores, object files or deployment secrets.

For those installations, quiesce **all** gateway/worker/Temporal replicas and other writers, take
consistent Redis and database snapshots, and capture object data plus Secret-manager versions as
one recovery point. Use a protected PostgreSQL service file for credentials, for example
`PGSERVICE=aw-gateway pg_dump -Fc --file=gateway.dump`; verify with `pg_restore --list gateway.dump`
and an actual restore into an isolated empty database. Hash these additional artifacts in the
operator's manifest. Preserve `WORKFLOW_SECRETS_KEY`, session/credential signing keys, authentication
configuration and provider-secret references; restored encrypted values need the original key.

Before reopening traffic, compare retained run IDs, settings revisions, revocations, budgets and
receipt heads with the captured inventory; verify `GET /v1/team/audit/verify`, resolve a seeded
workflow secret through an authorized activity, and resume a pending approval. A restored completed
step must keep its receipt and must not cause another provider/tool call. Keep another team as an
isolation control. Record backup time, restore time, observed data loss, checks and failures.

In a disposable Linux/WSL environment, `make workflow-restore-drill` runs an actual restore,
compares state and budgets, resumes an approval, checks receipt continuity and worker shutdown,
then stops and pauses Redis, Temporal and Temporal PostgreSQL in turn. Each fault must return a
bounded 503 while liveness stays healthy, and recovery with the same request ID must produce one
model receipt and charge. Faults are reversed in `finally`; only the drill's named volumes are
removed. Evidence is written under `.out/aw-hardening-*/result.json`. This must pass before a
backup is accepted as recoverable; native unit tests and checksum verification alone are insufficient.
