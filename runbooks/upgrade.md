# Upgrade And Rollback Runbook

Use this runbook for the most common day-2 operation: promoting a new platform
version, or rolling back to a previous one. Every Argo CD Application in this
repo runs `syncPolicy.automated` with `prune: true` and `selfHeal: true`, so
once a new revision is in Git, Argo CD applies it without a manual sync and
reverts any out-of-band change. That makes the promotion lever simple and
exact: the thing you change in Git is the thing that ships.

## What Moves When You Upgrade

The optional gateway PostgreSQL store has its own transactional schema migrations;
Temporal's PostgreSQL is separate. Redis is the default. See
[gateway storage upgrade and rollback](../docs/postgresql-storage.md#upgrade-and-rollback)
before changing backends: import existing Redis records with the offline command and select the
same backend on all replicas. SQL down migration 1 deletes gateway records, so back up first.
Include the optional database in backups alongside the Redis/Temporal stores.

A release bumps several pinned references together. `make production-check`
enforces that they all match the latest `CHANGELOG.md` version:

- the `inference-gateway` and `rag-service` chart image tags (`deploy/charts/*/values.yaml`),
- every chart `version` and the service `appVersion`,
- `SERVICE_VERSION` in the gateway and RAG service code and the OpenAPI `info.version`,
- the `CUSTOMER_REVISION=<tag>` examples in `README.md`, `docs/getting-started.md`, and `deploy/clusters/customer/README.md`,
- the `docs/index.md` Maturity admonition ("Current release `vX.Y.Z`").

CI builds and signs the images at that tag (see the build/release controls in
`docs/threat-model.md`). An operator promoting a published release points GitOps
at the tag whose images already exist.

## The Promotion Lever: CUSTOMER_REVISION

The customer overlay deploys from an immutable Git tag. The root
Application `deploy/gitops/argocd/root-app-customer.yaml` is pinned to an immutable
release tag (see the file for the current value) and every child Application in `deploy/clusters/customer/apps.yaml`
runs under the `agentworkflows` AppProject (`deploy/clusters/customer/appprojects.yaml`),
which locks `sourceRepos` to the approved repo. **Moving `CUSTOMER_REVISION` to a
new tag is the canonical promotion.** `make customer-overlay-check` accepts
only tags (it rejects `HEAD` or a branch), so every deployed state is reproducible and revertible.

Promote by re-running the overlay configurator with the new tag and committing:

    make customer-overlay \
      CUSTOMER_REPO_URL=https://github.com/<customer>/<repo>.git \
      CUSTOMER_REVISION=<new-tag> \
      CUSTOMER_GPU_PROFILE=<nvidia|amd|default>

    make customer-overlay-check

This rewrites `deploy/gitops/argocd/root-app-customer.yaml` and the child Applications
in `deploy/clusters/customer/apps.yaml` to the new tag. Commit to the customer repo;
Argo CD picks it up on the next poll. See `deploy/clusters/customer/README.md` for the
overlay mechanics.

## Order Of Operations

Upgrade in dependency order so each component serves traffic against a
matching store:

1. **Read the changelog** for the target tag. Note any data-store, schema, or
   collection-version changes (Qdrant `collectionVersion`, vector dimensions).
2. **Test the tag before promoting** (next section) in a lab or staging cluster.
3. **Promote stateful and platform dependencies first** if the release changes
   them: vector store (Qdrant) and budget Redis, then the RAG service, then the
   inference gateway and runtimes. Argo CD sync waves and health gating handle
   ordering within a sync, but stage risky data migrations explicitly.
4. **Move `CUSTOMER_REVISION` to the new tag** and commit. Argo CD syncs
   automatically; watch application health.
5. **Smoke test** the gateway and RAG paths:

       GATEWAY_URL=http://127.0.0.1:8080 make eval
       GATEWAY_URL=http://127.0.0.1:8080 make loadtest

   and confirm `make release-gate-strict` passes against current evidence.

## Test A Tag Before You Promote

Run every tag in a lab or staging cluster before production. Deploy the candidate tag
to a lab or staging cluster (or the local kind lab) and run the same gates:

    # Point a non-production overlay/cluster at the candidate tag, then:
    make eval
    make loadtest
    make release-gate-strict

Run the relevant chaos and restore checks for anything the release touches:
`runbooks/chaos-drills.md` for rollout/recovery and the RAG fault-injection
drill, and `runbooks/restore-drill.md` (especially the real Qdrant
data-recovery drill) before any release that changes the vector store. Promote
a tag once its evidence passes.

## Watch The Upgrade Land

    kubectl -n argocd get applications
    argocd app list
    argocd app get agentworkflows-root

Healthy + Synced across all applications means the new tag is live. If an
Application is Degraded or OutOfSync, follow the symptom-specific runbook:
`runbooks/incident-inference-runtime.md` for gateway/runtime,
`runbooks/rag-service.md` for RAG, `runbooks/policy-blocked-deploy.md` if Kyverno
admission rejected the sync.

## Rollback

Because the revision is a tag and the AppProject locks the source, rollback is
"point Git back at the last known-good tag." Prefer this to ad-hoc kubectl edits,
which selfHeal will revert anyway.

### Preferred: revert to the previous tag (GitOps-native)

    make customer-overlay \
      CUSTOMER_REPO_URL=https://github.com/<customer>/<repo>.git \
      CUSTOMER_REVISION=<previous-good-tag> \
      CUSTOMER_GPU_PROFILE=<nvidia|amd|default>
    make customer-overlay-check
    # commit; Argo CD syncs back to the previous tag automatically

This is the right rollback for a bad release: it is reproducible, leaves an audit
trail in Git, and keeps automation in charge.

### Fast: argocd app rollback

For an urgent single-application revert to a previously synced revision without a
Git change yet, use Argo CD's deployment history:

    argocd app history agentworkflows-root
    argocd app rollback agentworkflows-root <history-id>

This is a stopgap: the Git revision still points at the bad tag, so reconcile Git
(revert `CUSTOMER_REVISION`) afterward to keep selfHeal and the next sync on the
good tag.

## Pausing selfHeal And Prune For A Manual Rollback

A manual recovery (restoring data, editing a live resource, draining a node)
competes with `selfHeal` and `prune`, which revert live changes and prune resources
that are absent from Git. Pause automation on the affected Application for the
duration, then re-enable it.

Disable automated sync while you work:

    argocd app set <application> --sync-policy none

Re-enable automation (with prune and selfHeal) when the manual step is done and
Git matches the intended state:

    argocd app set <application> --sync-policy automated --auto-prune --self-heal

You can also pause by editing the Application's `spec.syncPolicy.automated` in
Git, but for a time-boxed manual rollback the `argocd app set` form is faster and
self-documenting in the audit log. Always re-enable automation before closing the
incident so the Application stays reconciled with Git.

## Evidence

Record the from/to tags, the `argocd app history` entry used (if any), the
commit that moved `CUSTOMER_REVISION`, smoke and gate results
(`make eval`, `make loadtest`, `make release-gate-strict`), and, if automation
was paused, when it was disabled and re-enabled.


## rc.3 compatibility checks

The gateway tests contain selected reliability configuration and Helm values captured from the
actual 0.2.0 and rc.2 commits (source hashes are in `tests/fixtures/upgrade`). With Helm on PATH
and `helm dependency build deploy/charts/workflows` complete, run
`python -m pytest src/inference-gateway/tests/test_upgrade.py src/inference-gateway/tests/test_storage.py`.
These checks load legacy environment values and render current gateway/worker charts with old
values, retaining store connections, task queues, timeouts and Secret references.

Set `TEST_REDIS_URL` and `TEST_POSTGRES_DSN` to disposable stores and also run
`src/inference-gateway/tests/test_postgres_integration.py`. The Redis checks use unique key prefixes
and cover unversioned/0/1/2 state, TTL preservation, interrupted migration replies and future-version
refusal. PostgreSQL checks use disposable schemas and verify timeout recovery and transactional DDL
rollback before retry. rc.3 keeps the existing schema version. SQL down migration 1 deletes gateway records.

On Linux/WSL, `make workflow-upgrade-test` upgrades actual 0.2.0 Compose state and
`make workflow-helm-upgrade-test` upgrades charts and images in disposable kind. These live checks
prove upgrade behavior end to end on real state.
