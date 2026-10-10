# Run a single-tenant service

Give one customer one namespace, one gateway URL, dedicated database credentials,
a Redis instance, and a persistent secret-encryption key. The customer uses the console;
your operations team owns upgrades, backups, identity, availability and provider access.
This is a reference deployment you operate. AgentWorkflows does not sell a hosted SLA.

## Choose the path

| Path | Command | Result |
| --- | --- | --- |
| Local evaluation | `docker compose -f deploy/compose/compose.yaml up --build -d --wait workflow-worker` | Gateway, console, Temporal, persistent stores, worker and a simulated provider; no paid key needed. |
| Disposable kind trial | `python scripts/first-approved-run.py kind` | Isolated cluster, template install, approved run and audit verification; prepare source images first. |
| Customer namespace | Helm reference below | HTTPS console, company sign-in, two gateway/worker replicas, external stores and explicit gateway egress. |

Use the [quickstart](quickstart.md) for kind's image-build and dependency commands.
The trial has a **300-second** deadline after images are prepared. The README's
60-second walkthrough covers console interaction on a ready stack. Neither number is a
cold image download or cluster provisioning benchmark.

## Prepare a tenant

Start with [values-single-tenant.yaml](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/charts/agentworkflows/values-single-tenant.yaml).
Copy it into your deployment configuration and replace the example domains, OIDC application
and group names. Use the same reviewed values for every upgrade.

Provision managed PostgreSQL databases `gateway`, `temporal`, and
`temporal_visibility`, and authenticated Redis with persistence and backups.
Use separate gateway and Temporal database users. The Temporal user needs schema DDL
privileges during upgrades; database creation remains an operator action.
The reference enables verified PostgreSQL TLS for Temporal. Use a certificate whose
name matches `serverName`; configure the provider CA through Temporal's chart mounts
when it is not in the image trust store.

Create these Kubernetes Secrets in the tenant namespace from your secret manager:

| Secret | Entry | Required value |
| --- | --- | --- |
| `agents-redis` | `url` | Authenticated `rediss://` URL with certificate verification |
| `agents-records` | `dsn` | Gateway PostgreSQL DSN with `sslmode=verify-full` |
| `agents-temporal-db` | `password` | Password for the configured Temporal database user |
| `agents-encryption` | `fernet-key` | Persistent Fernet key; retain it across reinstalls |
| `agents-oidc` | `oidc-client-secret` | OIDC application's client secret |
| `agents-tls` | `tls.crt`, `tls.key` | Certificate and key for the console hostname |

Keep values files free of plaintext credentials. Generate the encryption key once with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
on your secure administration host and store it in the secret manager.

Set `networkPolicy.externalEgress` to reviewed CIDRs and ports for managed Redis,
gateway PostgreSQL, providers and your identity provider. An empty list deliberately
blocks those connections. The CNI must enforce NetworkPolicy. Public provider IPs change;
maintain the approved list or use a controlled network egress layer. No public
`0.0.0.0/0` or `::/0` rule is accepted.

The umbrella policy isolates the gateway and any bundled stores. Apply your platform's
namespace isolation policy to Temporal, workers and research tools as well; their
intra-namespace RPC is not configured for mutual TLS by this profile. Keep Temporal
and database services private. The Research example's tools are synthetic; replace
them before treating publication as a business integration.

## Install and verify

Build candidate gateway and worker images from this checkout, make them available in
your registry, and pin their digests in `tenant-values.yaml`. The chart still has
older release defaults; do not rely on its default image tags for rc.4.

```bash
helm dependency update deploy/charts/workflows
helm dependency update deploy/charts/agentworkflows
helm lint deploy/charts/agentworkflows -f deploy/charts/agentworkflows/values-single-tenant.yaml -f tenant-values.yaml
helm upgrade --install agents deploy/charts/agentworkflows -n tenant-acme --create-namespace -f deploy/charts/agentworkflows/values-single-tenant.yaml -f tenant-values.yaml --wait --timeout 15m
```

Read the generated bootstrap admin Secret through your normal privileged access
process. Sign in over HTTPS, configure a provider in **Get started**, and complete
the sample approval. Test company sign-in with each mapped role, then use
**Data & privacy → Deployment checklist**.

`GET /v1/team/deployment` returns `checks[]` with `id`, `name`, `configured`
and `action`, plus `verification_required[]`. It requires an unrestricted team admin.
Python: `client.deployment()`. TypeScript: `client.deployment()`.
It reports settings without secrets or connection strings. A configured check does
not prove database TLS, backup recovery, egress enforcement or service availability.
Check `/readyz`, then prove the install end to end before admitting users.

### Prove the install

`agentworkflows check` walks the console's first-run path against any gateway, including this one:
it reads readiness, installs the sample if the key may, starts it, waits for the draft, approves it,
and confirms completion, the approval receipt, the run summary and (for an admin key) audit-chain
verification. It prints each stage's time against a 300-second budget and exits 1 at the first failure.

```bash
pip install ./sdk/python                      # or your internal package index
export AGENTWORKFLOWS_URL=https://agents.acme.example
export AGENTWORKFLOWS_API_KEY=...             # an admin key from your secret manager
agentworkflows check --allow-paid
```

The reference profile routes the sample to a real provider, so `--allow-paid` is required; the check
refuses to spend without it. A policy that needs several reviewers needs one more key per reviewer, passed
by environment variable name: `--approver-key-env SECOND_REVIEWER_KEY`. Keys are never accepted on the
command line. Use `--json` in CI. Run it after every install, upgrade and restore. A passing check is
evidence that approvals, workers, Temporal, storage and the audit chain work together; it does not prove
backups, network egress rules or sign-in with company accounts.

## Back up a recoverable unit

Redis holds budgets, sessions, key records and invitation tokens even when gateway
records use PostgreSQL. The legacy multi-namespace platform's Redis-is-ephemeral
assumption **does not apply** to this deployment.

Back up these together:

1. Gateway PostgreSQL, both Temporal databases, and Redis persistence.
2. Tenant values, chart version, image digests, policies and secret-manager versions.
3. The encryption key, provider credentials, OIDC secret, bootstrap and worker keys.
4. Exported audit receipts and an independently stored chain anchor.

For a consistent recovery point, stop incoming API traffic, pause triggers, drain
activities, scale workers and gateways to zero, then stop Temporal writers. Record
the original replica counts first. Take managed-store snapshots after writes stop.
A PostgreSQL logical backup can use libpq service definitions (which keep passwords
out of command arguments):

```bash
pg_dump --dbname=service=aw-gateway --format=custom --file=gateway.dump
pg_dump --dbname=service=aw-temporal --format=custom --file=temporal.dump
pg_dump --dbname=service=aw-temporal-visibility --format=custom --file=temporal-visibility.dump
```

Export the managed Redis snapshot in the same stopped interval. Encrypt backups in
an access-controlled off-cluster store, record checksums and secret versions, then
restore Temporal, gateways and workers to their recorded replica counts and reopen
traffic. Record the interruption duration and backup timestamp. Choose and measure
your RPO/RTO; this reference makes no unmeasured recovery guarantee.

Restore quarterly and before a database migration in an isolated namespace with
outbound provider/tool access blocked. Restore PostgreSQL and Redis to the same
checkpoint and restore the matching encryption key. Do not mix an old Temporal
snapshot with current gateway records. Use `pg_restore --exit-on-error` into empty
databases, restore Redis through the managed service, then start the matching
application versions. Verify an existing run, approval receipts, budget balances,
key revocations and decryption before a new approved run. Revoke restored sessions
and outstanding invitation credentials if the recovery point predates revocation.
Keep triggers paused until operators reconcile external side effects.

## Upgrade without losing history

1. Read the changelog and [gateway migration rules](postgresql-storage.md#upgrade-and-rollback).
   Take the coordinated backup above and verify the checksums.
2. Restore that backup into staging. Run the new image versions against it, inspect
   a pre-upgrade run, then run `agentworkflows check` to complete a new approval and verify the audit chain.
3. Preserve database, key and policy Secrets. Supply the complete reviewed values;
   avoid inheriting obsolete settings with `--reuse-values`.
4. Run the same `helm upgrade --install` command with reviewed new image digests.
   Watch migration jobs, rollout health and `/readyz`; exercise sign-in, then run `agentworkflows check`.
5. Keep the old images and checkpoint until acceptance finishes. Roll back application
   images only when their storage schema remains compatible. Otherwise restore the
   coordinated checkpoint into an isolated recovery environment. A Helm rollback
   does not reverse a database migration or an external tool action.

Run the Compose recovery and upgrade drills in
[release verification](release-verification.md) on a Docker-capable host before promotion.
