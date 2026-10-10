# Kubernetes production checklist

Use this with the [umbrella chart installation guide](install-kubernetes.md). Its default
install is a single-node evaluation footprint. Enable the optional controls below and
record the results of your own cluster checks before serving a team.

## TLS and access

- [ ] Set `inference-gateway.ingress.enabled`, `className`, `host`, and `tls.enabled`;
  supply `tls.secretName` or a cert-manager issuer annotation that creates that Secret.
  Verify certificate trust, hostname, renewal and your controller's HTTP-to-HTTPS redirect.
- [ ] Verify `/console/` and `/readyz` through HTTPS. Chart-managed TLS forces
  `SESSION_COOKIE_SECURE=true`; confirm the browser's `aw_session` cookie is Secure.
  For TLS terminated elsewhere, set `inference-gateway.adminConsole.cookieSecure=true`.
- [ ] Keep the gateway Service internal and its dedicated metrics port private. Enable
  the umbrella NetworkPolicy with reviewed provider, identity-provider and datastore
  destinations; test both allowed and denied traffic on an enforcing CNI.

## OIDC and secrets

- [ ] Register the exact HTTPS `/v1/auth/callback` URL. Set issuer, client ID,
  `existingSecret`, scopes including `openid`, and controlled team/role/project claims.
  An empty redirect value derives from TLS ingress. Start with `defaultRole: viewer`.
- [ ] Verify sign-in, refresh, CSRF-protected changes and logout. Test users from an
  unauthorized team, a viewer, and an admin. Only trusted IdP administrators should
  control the claims that grant team membership and roles.
- [ ] Store provider credentials, OIDC client secret, Redis URL, PostgreSQL password,
  worker key and bootstrap admin key in your secret manager. Restrict Kubernetes Secret
  access and keep an audited bootstrap credential for identity-provider outages.
- [ ] Rotate provider/OIDC/Redis credentials in their upstream system and corresponding
  existing Secret, allowing overlap where supported. Restart consumers to reload env:

  ```bash
  kubectl rollout restart deployment/inference-gateway -n aw
  kubectl rollout status deployment/inference-gateway -n aw --timeout=5m
  # If enabled and its Redis or object-store credentials changed:
  kubectl rollout restart deployment/inference-gateway-batch-processor -n aw
  ```

  For PostgreSQL, change the database role password as well as the Secret, then restart
  Temporal's server deployments; the existing database keeps its role password until you
  change it there. Supply the new Secret to both datastores and future schema Jobs.
  To rotate bootstrap/worker keys, update their Secrets, run the normal Helm upgrade to
  regenerate `agentworkflows-key-records`, and restart gateway/worker deployments. Verify
  old keys fail. Rotate Redis-managed user keys through Members & keys or the CLI.
  OIDC client-secret rotation leaves existing browser sessions active: for an identity
  incident, invalidate sessions separately using the sessions' Redis key prefix.

## Availability and sizing

- [ ] Enable at least two gateway replicas, the PDB (`minAvailable: 1`) and anti-affinity.
  Check placement on two nodes and reserve capacity for the rolling-update surge pod.
  Requests/limits default to `100m`/`128Mi` and `500m`/`512Mi` per gateway; size from measured
  concurrency, latency and memory, then rehearse a pod failure and node maintenance.
- [ ] Choose an HA Redis and PostgreSQL service when your SLO requires it. Gateway
  replicas cover the gateway tier; the bundled stores and Temporal run as single replicas.
  Plan Temporal server and worker capacity independently. Keep shared state in Redis;
  use shared S3 and Redis if enabling Batch, and Redis for enabled Responses/cache state.
- [ ] Check dependency readiness, provider limits, disk space, Redis eviction and
  connection counts. Review team/workflow budgets and provider price estimates.

## Backups and restore

- [ ] Define an RPO/RTO, backup schedule, encryption, off-cluster storage and access policy.
  Back up a coordinated set after quiescing admissions/workers as needed; take Redis and
  Temporal copies at the same point so they describe the same workflow state.
- [ ] Back up **Redis AOF and/or a verified RDB snapshot** for every logical database used.
  The bundled Redis uses AOF with `appendfsync always` and disables periodic RDB saves.
  Modern multipart AOF backups need the manifest, base and incremental files together;
  capture them with a consistent volume snapshot or the [Redis backup procedure](https://redis.io/docs/latest/management/persistence/).
  Include budgets, workflow records/content, sessions, managed keys,
  team settings, audit views and audit heads; optional cache/Batch/Responses stores also
  use Redis. Persistence and backups are separate controls; keep both.
- [ ] Back up **Temporal PostgreSQL**, both `temporal` and `temporal_visibility` (or your
  configured names), plus roles/grants. Use your database service's snapshot/PITR or
  verified dumps; preserve the schema/server versions needed for recovery.
- [ ] Save matching credential Secrets securely, your release values, image digests,
  chart versions, routing/team policies and external receipt logs/head anchors. Include
  S3 Batch blobs or other optional stores if enabled. Keep exported Secrets out of version control.
- [ ] Restore into an isolated namespace and exercise an existing run waiting for approval,
  managed-key login, budgets, team settings and audit verification. Recover both stores
  and matching Secrets before admitting new traffic. Record the actual recovery time.
  Disabling bundled Redis deletes its Helm-managed PVC; disabling PostgreSQL leaves its
  StatefulSet claim. Back up before external-datastore migration or uninstall.

## Upgrade and rollback

- [ ] Pin chart/image versions and keep all overrides in a reviewed values file. Back up
  first; review release notes, new defaults and Temporal schema compatibility. Render/lint
  the exact upgrade values and rehearse on restored data before production.

  ```bash
  helm dependency update deploy/charts/workflows
  helm dependency build deploy/charts/agentworkflows
  helm lint deploy/charts/agentworkflows --strict -f production-values.yaml
  helm template aw deploy/charts/agentworkflows -n aw -f production-values.yaml > /tmp/aw-review.yaml
  helm history aw -n aw
  helm upgrade aw deploy/charts/agentworkflows -n aw -f production-values.yaml --wait --timeout 15m
  kubectl rollout status deployment/inference-gateway -n aw --timeout=5m
  ```

  Offline renders generate example bootstrap credentials, since `lookup` reads live
  Secrets only during an install or upgrade against the cluster. Protect the render output and use `helm upgrade` for a running release.
  Keep namespace, release and credential Secret names stable; live upgrades reuse them.
  Verify `/readyz`, sign-in, an existing approval and a new run after upgrading.
- [ ] Record a known-good Helm revision and test rollback before relying on it:

  ```bash
  helm history aw -n aw
  REVISION=1  # Replace with the reviewed, compatible revision.
  helm rollback aw "$REVISION" -n aw --wait --timeout 15m
  ```

  Helm rollback restores Kubernetes resources; Redis data, PostgreSQL schema and
  upstream credentials stay as they are. Roll Temporal back only between compatible
  versions; follow Temporal's version guidance and restore a compatible backup set when required.
  The external-PostgreSQL example runs schema hooks before upgrade; with bundled
  PostgreSQL, inspect the rendered schema Job and verify it completed successfully.

## Retention

Set retention deliberately for each data class. These gateway values are nested under
`inference-gateway` in the umbrella chart:

| Value | Default | Data affected |
| --- | --- | --- |
| `workflowRecords.runRecordRetentionSeconds` | 2592000 (30 days) | Terminal run records; active runs remain |
| `workflowRecords.contentRetentionSeconds` | 604800 (7 days) | Opt-in captured step input/output |
| `workflowRecords.contentMaxBytes` | 16384 | Maximum bytes per captured value |
| `traceability.auditViewRetentionSeconds` | 7776000 (90 days) | Redis audit view |
| `responses.store.retentionSeconds` | 86400 (1 day) | Optional Responses content |
| `batch.retentionSeconds` | 604800 (7 days) | Optional Batch records/blobs; verify object-store cleanup |
| `responseCache.ttlSeconds` | 60 | Optional response cache |

- [ ] Review each workflow's `captureContent` policy (`none`, `redacted`, `full`) and
  `contentMaxBytes`. Redis TTLs apply to Redis; set retention for raw inputs/results in
  Temporal history, exported logs, backups and third-party systems separately.
- [ ] Set Temporal namespace retention independently under
  `workflows.temporal.server.config.namespaces.namespace` (the default namespace is `30d`).
  For an existing namespace, apply the change through Temporal administration; the
  chart's namespace-creation Job sets retention when it creates a namespace:

  ```bash
  temporal operator namespace update --namespace default --retention 30d
  ```

  Connect the CLI to your Temporal frontend with its required credentials; see the
  [Temporal namespace command reference](https://docs.temporal.io/cli/operator).
  Configure log, object-store and backup expiration separately; retain external receipt anchors
  as required by your audit policy. Browser sessions have a 12-hour idle timeout and a
  seven-day maximum.

For the wider operational controls, see [production readiness](production-readiness.md)
and the [disaster recovery runbook](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/disaster-recovery.md).
