# Disaster Recovery Runbook

This runbook defines the single-cluster disaster-recovery (DR) posture for the platform: what the platform
backs up, the recovery-point and recovery-time targets those backups imply, the order in which the
platform is restored, and the scope the operator owns.

Scope is one cluster: the deployment templates cover a single-cluster rebuild. Secondary clusters,
multi-region topologies and warm standby are operator-owned and are listed under
[Operator-Owned Scope](#operator-owned-scope).

For a confirmed data-loss or data-exposure event treat this as **SEV1** and start from the
[incident-response index](incident-response.md) first; this runbook is the recovery procedure that
the SEV1 path routes into. For backup-tooling failures (a drill or backup that did not complete) use
[restore-drill.md](restore-drill.md).

## What AgentWorkflows Backs Up

Two complementary mechanisms ship in `deploy/backup`:

- **Velero schedule** (`deploy/backup/velero/schedule.yaml`). A daily `Schedule` named
  `ai-platform-daily` running at `0 2 * * *` with `ttl: 168h0m0s` (7-day retention). It backs up the
  **data-bearing namespaces** and sets `defaultVolumesToFsBackup: true` so PVC *contents* are
  captured along with resource metadata. The included namespaces are:
  - `vector`: Qdrant vector-store PVCs (RAG embeddings)
  - `ai-agents`: agent-workspace PVCs
  - `argocd`: GitOps / Argo CD application and sync state
  - `ollama`: local model store
  - `restore-drill`: restore-drill evidence consumed by the smoke test

  Gateway and workflow state in `budget`, `inference` and `workflows` uses the coordinated backup
  procedure below: Redis (durable application state when workflows are enabled), optional gateway
  PostgreSQL, Temporal databases, file/object storage and encryption/signing keys. The stateless
  model runtimes are reconstructed from images and model pulls.

- **restore-drill** (`deploy/backup/restore-drill`, runbook [restore-drill.md](restore-drill.md)).
  This *validates* that the backups are recoverable. The Kubernetes CronJob
  (`deploy/backup/restore-drill/k8s/cronjob.yaml`) runs daily at `0 3 * * *`, one hour after the
  Velero schedule. Two drills exist and prove different things: the default Redis-AOF smoke proves
  the restore *tooling* runs, while `RUNTIME=local RESTORE_DRILL_QDRANT_DATA=1 make restore-drill`
  proves real Qdrant vector data is recoverable end to end. See
  [restore-drill.md](restore-drill.md) for the evidence each drill produces.

The operator provides the Velero `BackupStorageLocation` and `VolumeSnapshotLocation` and the
object-storage target behind them (see [Operator-Owned Scope](#operator-owned-scope) and the
customer handoff checklist in `deploy/clusters/customer/README.md`). Keep CSI volume snapshots or
`defaultVolumesToFsBackup` in effect for the PVC-bearing namespaces so restores bring back the data
along with the resources.

## RPO And RTO Targets

**RPO (recovery point objective)** is bounded by backup cadence. With the shipped Velero schedule at
`0 2 * * *` and AgentWorkflows' snapshot/AOF cadence, the worst-case data-loss window for the protected
data stores is **up to 24 hours**: the time between the last completed daily backup and the
failure. Stores that also keep their own continuous journal (Redis AOF, where used) recover closer
to the failure. Plan against the 24-hour bound, or raise the schedule frequency or add CSI snapshot
intervals for a tighter one. These cadences are operator-tunable: shorten the Velero
`schedule` and add a `VolumeSnapshotLocation` to tighten RPO.

**RTO (recovery time objective)** is the time to a serving platform after the decision to recover. It
is dominated by data restore time (Qdrant snapshot size, agent-workspace PVC size) and runtime
warm-up (image and model re-pull; the model weights take longest, especially large vLLM models).
As a single-cluster planning target, aim for **bring-up within a few hours** for a moderate data
footprint. Larger model weights and vector collections lengthen the runtime and vector-store steps.
Measure your own RTO with the platform's real restore drill.

> These are *targets for a single-cluster rebuild from good backups*, with the operator-owned
> off-cluster backup target and Velero locations in place and healthy (see
> [Operator-Owned Scope](#operator-owned-scope)).

## Per-Store Data-Loss Window

| Store | Namespace | Backed up by | Worst-case loss on cluster failure | Notes |
| --- | --- | --- | --- | --- |
| Qdrant vector store | `vector` | Velero daily (PVC contents) + Qdrant snapshots | Up to 24h of newly ingested vectors | Rebuildable from the source-of-truth knowledge ingestion if no good snapshot exists (re-embed). |
| Agent-workspace PVCs | `ai-agents` | Velero daily (PVC contents) | Up to 24h of uncommitted workspace state | Anything pushed to a Git remote is safe; the window applies to un-pushed local working state. |
| Argo CD / GitOps state | `argocd` | Velero daily; also reconstructable from Git | Effectively zero for desired state | The Git repo is the source of truth; Velero restores app/sync state faster than re-bootstrapping. |
| Ollama model store | `ollama` | Velero daily; also re-pullable | Up to 24h; re-pullable | Models are re-pullable from the registry/model store, so this backup is a convenience. |
| Gateway/budget Redis | `budget` (or the installation namespace) | Coordinated RDB/AOF backup, separate from the shipped Velero schedule | Since the last consistent snapshot | Contains accounting, sessions, secrets and, with the Redis storage backend, runs, settings, keys and receipts. Always restore it from backup. |
| Gateway PostgreSQL (optional) | `inference` or external | Coordinated `pg_dump -Fc` or managed-store snapshot | Since the last consistent snapshot | Gateway records and schema version; restore Redis accounting alongside it. |
| Temporal PostgreSQL | `workflows` or external | Both `temporal` and `temporal_visibility` databases | Since the last consistent snapshot | Preserve workflow history and visibility together; independent of gateway SQL schema. |
| Inference / vLLM runtimes | `inference`, `vllm` | GitOps (stateless) | None (stateless) | Reconstructed by GitOps + image/model re-pull. |

## Whole-Platform Recovery Sequence

Recover in dependency order. Each step builds on a healthy previous step, so admission passes and
every store comes back with its data. Before starting, classify the incident and page per
[incident-response.md](incident-response.md), and pause Argo CD auto-sync for the applications
being restored (see [upgrade.md](upgrade.md)).

1. **Cluster + Argo CD / GitOps state first.** Stand up the cluster (or the rebuilt one), install
   Argo CD, and restore the `argocd` namespace, either by Velero restore or by re-bootstrapping
   from Git (`make bootstrap-argocd`, then `make sync`; for the customer overlay,
   `ENVIRONMENT=customer make bootstrap-argocd && ENVIRONMENT=customer make sync`). *Why first:*
   GitOps is the control plane that schedules everything else. The Git repo is the source of truth
   for desired state, so the platform's shape is always reconstructable from Git; restore it before the data stores so the workloads have somewhere to land.

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
   the smaller PVCs first. Anything pushed to a Git remote is intact.

4. **Runtimes via image + model re-pull.** Let GitOps reconcile `inference` and `vllm`; these are
   rebuilt entirely from image pulls and model-weight pulls. *Why after the data stores:* the
   runtimes are stateless and re-pullable, so they come last among the serving components: they
   warm up rather than restore. This is typically the slowest step (large model weights); start the
   pulls as early as the cluster allows and run the data restores in parallel. Confirm the model runtime has ready endpoints
   (see [incident-inference-runtime.md](incident-inference-runtime.md)).

5. **Gateway and workflow state before reopening ingress.** Restore Redis, both Temporal databases,
   optional gateway PostgreSQL, object data and the original encryption/signing keys from one
   recovery point. Keep gateway and workers stopped until the stores are verified, then start
   Temporal, gateway and workers in that order. Run the checks below before accepting new work.
   Restore Redis and Temporal together so completed effects run once and budget holds are kept.

After all steps, run the smoke and evidence checks before declaring recovery: `make eval` (and
`make loadtest`) against the gateway, RAG retrieval smoke, and, for the recovered vector store, a
re-confirmation of point counts. Capture the full timeline and the restore artifacts as incident evidence
per [incident-response.md](incident-response.md).

## Operator-Owned Scope

AgentWorkflows ships manifests, charts, service code, validation tooling, and runbooks. The
operator owns the following for DR beyond a single-cluster rebuild:

- **Off-cluster backup target.** Velero needs a `BackupStorageLocation` (object storage) and a
  `VolumeSnapshotLocation` that the operator provisions and credentials. The platform ships the schedule
  template and the data-bearing namespace list; the configured off-cluster target carries the
  backups through a cluster loss and gives the restore drill real data to validate. See the handoff
  checklist in `deploy/clusters/customer/README.md`.
- **Secondary cluster.** Cross-cluster restore (Velero restore into a fresh cluster) is
  operator-driven.
- **Multi-region / warm standby.** Region failover, replication, and a warm-standby topology are
  operator-owned.
- **Backup-target and snapshot scheduling decisions.** The Velero cadence and retention, CSI
  snapshot intervals, and any tightening of RPO are per-environment operational decisions; the platform
  ships sane defaults the operator tunes.

## Related Runbooks

- [Incident response index](incident-response.md): severity tiers and the SEV1 path that routes here.
- [Restore drill](restore-drill.md): backup-tooling smoke and the real Qdrant data-recovery drill.
- [Budget controls](budget-controls.md): budget posture while Redis recovers.
- [Inference runtime incident](incident-inference-runtime.md): bringing runtimes back to ready.
- [Upgrade & rollback](upgrade.md): pausing Argo CD automation during a controlled change.
- [Runbooks index](README.md): the full runbook catalog.


## Gateway and worker backup verification

For the default Redis-backed Compose installation, the existing script stops workers, gateway
and Temporal, exports both PostgreSQL databases and Redis, collects receipts, records SHA-256
checksums and an audit anchor, then restarts the source whether the backup succeeds or fails. Quiesce external
writers and ingress first. Protect the backup directory as sensitive data and copy it off-host;
retain its manifest and audit anchor independently so the trust anchor stays separate from the
backup itself. The RPO for these additional stores follows the cadence at which you run this backup.

```sh
python3 scripts/workflow-recovery.py backup --project source-project --directory /secure/aw-backup --receipts /secure/retained-receipts.jsonl
python3 scripts/workflow-recovery.py verify --directory /secure/aw-backup
python3 scripts/workflow-recovery.py restore --project new-isolated-restore --directory /secure/aw-backup
```

Use the actual source Compose project and a new, empty restore project. `restore` requires stopped
consumers and empty stores. `verify` is read-only and runs natively without Docker: it checks for
missing/empty files, checksum mismatches and broken receipt chains. To prove a database restores,
run an actual restore. The default helper covers Redis-backed gateway records and Temporal; for
optional gateway PostgreSQL, external stores, object files and deployment secrets, use the
procedure below.

For those installations, quiesce **all** gateway/worker/Temporal replicas and other writers, take
consistent Redis and database snapshots, and capture object data plus Secret-manager versions as
one recovery point. Use a protected PostgreSQL service file for credentials, for example
`PGSERVICE=aw-gateway pg_dump -Fc --file=gateway.dump`; verify with `pg_restore --list gateway.dump`
and an actual restore into an isolated empty database. Hash these additional artifacts in the
operator's manifest. Preserve `WORKFLOW_SECRETS_KEY`, session/credential signing keys, authentication
configuration and provider-secret references; restored encrypted values need the original key.

Before reopening traffic, compare retained run IDs, settings revisions, revocations, budgets and
receipt heads with the captured inventory; verify `GET /v1/team/audit/verify`, resolve a seeded
workflow secret through an authorized activity, and resume a pending approval. Confirm that a restored
completed step keeps its receipt and makes no further provider/tool call. Keep another team as an
isolation control. Record backup time, restore time, observed data loss, checks and failures.

In a disposable Linux/WSL environment, `make workflow-restore-drill` runs an actual restore,
compares state and budgets, resumes an approval, checks receipt continuity and worker shutdown,
then stops and pauses Redis, Temporal and Temporal PostgreSQL in turn. Each fault must return a
bounded 503 while liveness stays healthy, and recovery with the same request ID must produce one
model receipt and charge. Faults are reversed in `finally`, and the drill removes only its own named
volumes. Evidence is written under `.out/aw-hardening-*/result.json`. Accept a backup as
recoverable once this drill passes.
