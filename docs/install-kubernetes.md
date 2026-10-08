# Install on Kubernetes

The umbrella chart installs the gateway and console, persistent Redis, Temporal with
PostgreSQL, a worker for the `default` team, and the Research example's tools in one
namespace. Ollama, vLLM, RAG and Qdrant are opt-in. The separate
[GitOps deployment](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/clusters/customer/README.md) keeps its existing defaults.

## Prerequisites

- Kubernetes, `kubectl`, Helm 3, and a default StorageClass that can provision the
  10 GiB PostgreSQL and 1 GiB Redis claims. A disposable kind cluster is sufficient.
- Image access to GHCR, Docker Hub and Temporal's images; HTTPS egress to your chosen provider.
- A checkout of the release: `git clone --branch v0.5.1 --depth 1 https://github.com/RamazanKara/agentworkflows.git`.

Prepare the local chart's dependencies once, in this order:

```bash
helm dependency update deploy/charts/workflows
helm dependency update deploy/charts/agentworkflows
```

Published umbrella packages include those dependencies. Component Service names are
fixed: install one release per namespace. No ingress controller, GPU, KEDA or
Prometheus CRDs are required.

## Install

```bash
helm install aw deploy/charts/agentworkflows -n aw --create-namespace --wait --timeout 15m
```

The chart generates `temporal-postgres-auth` (`password`), `workflow-gateway-key`
(`api-key`), and `agentworkflows-admin` (`api-key`) if they are missing. Pre-create
these Secrets to supply your own credentials. For different names, set
`workflows.postgres.existingSecret` (and both Temporal SQL datastore `existingSecret`
values), `workflows.worker.existingSecret`, or `bootstrapAdmin.existingSecret`.
Gateway key records contain hashes; the worker has execution scope and cannot approve.

## Sign in

Read the bootstrap admin key and keep it private:

```bash
kubectl get secret agentworkflows-admin -n aw -o jsonpath='{.data.api-key}' | base64 -d; echo
kubectl port-forward -n aw svc/inference-gateway 8080:8080
```

Open <http://127.0.0.1:8080/console/> and paste that key. The console uses the `default`
team and project. In another terminal, `curl -fsS http://127.0.0.1:8080/readyz` checks
Redis and Temporal. Missing provider credentials leave the console
available; **Providers & budgets** identifies the missing key and Secret setup.
Inference still rejects requests until a provider key is configured.

## Add a provider key

Create a Secret in the same namespace. This Bash example reads the key without echoing it:

```bash
read -rs -p 'OpenAI API key: ' OPENAI_API_KEY; echo
printf '%s' "$OPENAI_API_KEY" | kubectl create secret generic openai-api-key -n aw --from-file=api-key=/dev/stdin
unset OPENAI_API_KEY
helm upgrade aw deploy/charts/agentworkflows -n aw --reuse-values \
  --set providers.openai.existingSecret=openai-api-key --wait --timeout 15m
```

If the Secret already exists before installation, pass
`--set providers.openai.existingSecret=openai-api-key` to the install command instead.
For Anthropic, create `anthropic-api-key` with an `api-key` entry and set
`providers.anthropic.existingSecret=anthropic-api-key`. Only the gateway receives provider
keys, via Secret references. After rotating a provider key, run
`kubectl rollout restart deployment/inference-gateway -n aw` to refresh its environment.

The `research` route uses [GPT-4.1 Mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini);
`anthropic` uses [Claude Haiku 4.5](https://platform.claude.com/docs/en/about-claude/models/overview). Review the
routes, provider access and configured price estimates before paid calls. Set reviewed
routes under `inference-gateway.routing.policy.models` and team/workflow permissions under
`inference-gateway.sandboxPolicy.policy.policies`. There is no automatic cross-provider fallback.

With the Python SDK installed as in the [quickstart](quickstart.md), run:

```bash
export AGENTWORKFLOWS_URL=http://127.0.0.1:8080
export AGENTWORKFLOWS_API_KEY="$(kubectl get secret agentworkflows-admin -n aw -o jsonpath='{.data.api-key}' | base64 -d)"
agentworkflows runs start ResearchWorkflow --input '{"topic":"Evaluate agent reliability","model":"research"}'
RUN_ID='paste-the-run_id-here'
agentworkflows runs inspect "$RUN_ID"
# When progress.stage is awaiting_approval, read progress.draft before approving.
agentworkflows runs approve "$RUN_ID"
agentworkflows runs inspect "$RUN_ID"
```

Repeat inspection until `status: completed` and `result.status: published`. Use
`"model":"anthropic"` for that provider. The example's research source and publication
are synthetic; no document is published externally. Model calls are real and paid.
Replace the tool URLs with your integrations when adapting the workflow. The default
ceilings are 10,000 tokens / $5 per run and 200,000 tokens / $50 per team budget window.

## TLS ingress

Ingress, OIDC, gateway HA and the umbrella NetworkPolicy are opt-in. Keep the following
values files with your deployment configuration; the commands below update an existing
`aw` installation and preserve its other values. Use `helm install ... -f FILE` for a
fresh installation. Review the [Kubernetes production checklist](kubernetes-production-checklist.md)
before exposing the service.

Use an installed ingress controller, point your DNS host at it, and create a TLS Secret
in `aw` from your certificate and private key:

```bash
kubectl create secret tls agents-tls -n aw --cert=fullchain.pem --key=privkey.pem
cat > tls-values.yaml <<'YAML'
inference-gateway:
  ingress:
    enabled: true
    className: nginx
    host: agents.example.com
    annotations:
      nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    tls:
      enabled: true
      secretName: agents-tls
YAML
helm upgrade aw deploy/charts/agentworkflows -n aw --reuse-values -f tls-values.yaml --wait --timeout 15m
curl -fsS https://agents.example.com/readyz
```

Replace the host, class and controller-specific annotations. The `/` prefix serves both
the API and `/console/`; the metrics port is not exposed. TLS ingress automatically sets
`SESSION_COOKIE_SECURE=true`, overriding `inference-gateway.adminConsole.cookieSecure=false`.
The default port-forward install keeps HTTP localhost sessions working. After enabling
TLS, use the HTTPS console; HTTP port-forwarding no longer supports its Secure cookies.

For cert-manager, omit the `kubectl create secret tls` command and add
`cert-manager.io/cluster-issuer: letsencrypt` to `ingress.annotations` in the file above.
`secretName` is still required: cert-manager writes the issued certificate there. Supply
your own ClusterIssuer and verify issuance before signing in. The equivalent command is:

```bash
helm upgrade aw deploy/charts/agentworkflows -n aw --reuse-values -f tls-values.yaml \
  --set-string 'inference-gateway.ingress.annotations.cert-manager\.io/cluster-issuer=letsencrypt' --wait --timeout 15m
```

If TLS terminates outside this chart's Ingress, explicitly set
`inference-gateway.adminConsole.cookieSecure=true`.

## OIDC company sign-in

Register an authorization-code application with the exact callback
`https://agents.example.com/v1/auth/callback`. Configure an identity-provider-controlled
string claim `team` containing `default` for this installation; role and project claims
must also be scalar strings. See [Company sign-in](workflows.md#company-sign-in-oidc)
for supported roles and provider examples. Use the TLS values above, then:

```bash
read -rs -p 'OIDC client secret: ' OIDC_SECRET; echo
printf '%s' "$OIDC_SECRET" | kubectl create secret generic agents-oidc -n aw --from-file=oidc-client-secret=/dev/stdin
unset OIDC_SECRET
cat > oidc-values.yaml <<'YAML'
inference-gateway:
  auth:
    oidc:
      issuer: https://identity.example.com
      clientId: agentworkflows
      existingSecret:
        name: agents-oidc
        key: oidc-client-secret
      scopes: openid profile email
      teamClaim: team
      roleClaim: role
      projectClaim: project
      defaultRole: viewer
YAML
helm upgrade aw deploy/charts/agentworkflows -n aw --reuse-values -f tls-values.yaml -f oidc-values.yaml --wait --timeout 15m
curl -fsS https://agents.example.com/v1/auth/config
```

An empty `auth.oidc.redirectUrl` derives the HTTPS callback from the enabled TLS ingress
host, only when `issuer` is configured. An explicit `redirectUrl` takes precedence.
Without chart-managed TLS ingress, supply the explicit HTTPS callback and Secure cookie
setting yourself. Scopes must include `openid`; missing roles use `defaultRole` (viewer).
The client secret is referenced from the existing Secret, not copied into Helm values.
OIDC sessions and API-key sessions share the configured Redis store across gateway replicas.

## Gateway availability and resources

The default is one gateway replica with requests of `100m` CPU / `128Mi` memory and
limits of `500m` / `512Mi`, a starting point for a small team using cloud providers.
Measure load and adjust `inference-gateway.resources`. Enable two replicas and the PDB:

```bash
cat > ha-values.yaml <<'YAML'
inference-gateway:
  replicaCount: 2
  podDisruptionBudget:
    enabled: true
    minAvailable: 1
  antiAffinity:
    enabled: true
YAML
helm upgrade aw deploy/charts/agentworkflows -n aw --reuse-values -f ha-values.yaml --wait --timeout 15m
kubectl get pods -n aw -l app.kubernetes.io/name=inference-gateway -o wide
kubectl get pdb inference-gateway -n aw
```

Use at least two schedulable nodes. Anti-affinity prefers different hostnames; it permits
co-location when capacity is constrained, including the surge pod during a rolling update.
Verify placement and leave capacity for that extra pod. The PDB covers voluntary
disruptions; it does not protect against node failure or make Redis/PostgreSQL/Temporal
highly available. Keep Redis-backed budgets and audit heads; if you enable response
caching, Responses state or Batch, select their Redis backends too (Batch also needs S3).

## Network isolation

Use a CNI that enforces Kubernetes NetworkPolicy. The umbrella policy selects gateway
pods, admits HTTP from the named ingress controller namespace and the workflow worker
(also the Batch worker when enabled), and allows egress to CoreDNS, bundled Redis,
Temporal frontend, Research tools and enabled Ollama/vLLM pods. Bundled Redis accepts
only gateway/Batch-worker traffic and has no outbound connections. Other stack pods
retain their existing networking; this is not a namespace-wide isolation policy.

External egress is closed until you list destination CIDRs and TCP ports. Include your
providers, the OIDC discovery/token/JWKS endpoints, and external Redis if used. These are
documentation addresses; replace them with your approved destinations before applying:

```bash
cat > network-values.yaml <<'YAML'
networkPolicy:
  enabled: true
  ingressControllerNamespace: ingress-nginx
  externalEgress:
    - {cidr: 203.0.113.10/32, port: 443}  # Provider
    - {cidr: 203.0.113.20/32, port: 443}  # OIDC
    - {cidr: 192.0.2.10/32, port: 6379}  # External Redis, if used
YAML
helm upgrade aw deploy/charts/agentworkflows -n aw --reuse-values -f network-values.yaml --wait --timeout 15m
kubectl describe networkpolicy -n aw
```

Standard NetworkPolicy cannot allow DNS names; maintain provider IP ranges or deploy
your CNI's FQDN policy separately. There is no blanket HTTPS allow rule. Verify your CNI's
Service/NAT behavior and adapt DNS rules for NodeLocal DNS. Policies are additive, so
review other policies selecting these pods; keep the component `networkPolicy.enabled`
values false. See [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/).
Metrics scraping, custom tools, notifications, OTLP collectors, RAG and other integrations
need separately reviewed rules. The default `helm test` gateway probe is not an allowed
client with this policy; test through the ingress controller as shown below.

## External Redis

Use a persistent Redis service supporting the gateway's Redis commands and Lua scripts;
keep eviction disabled for durable state. Before changing an existing installation,
back up and migrate Redis data: disabling `budget-redis` removes its chart-managed PVC.
This setting does not copy data or turn the bundled Redis into a cluster.

```bash
read -rs -p 'Redis URL (rediss://user:password@host:6379/0): ' REDIS_URL; echo
printf '%s' "$REDIS_URL" | kubectl create secret generic agents-redis -n aw --from-file=url=/dev/stdin
unset REDIS_URL
cat > redis-values.yaml <<'YAML'
budget-redis:
  enabled: false
inference-gateway:
  redis:
    existingSecret:
      name: agents-redis
      key: url
YAML
helm upgrade aw deploy/charts/agentworkflows -n aw --reuse-values -f redis-values.yaml --wait --timeout 15m
```

The Secret holds the complete URL (percent-encode credentials); prefer `rediss://` with
a certificate trusted by the gateway image. It replaces all five Redis URL environments:
budgets/sessions/workflow records, audit heads, response cache, Batch and Responses state.
The Batch processor uses the same reference. Distinct key prefixes keep stores separate
in one logical database; preserve those prefixes and migrate all previously used databases.
The Secret is never generated or owned by this chart. The `redis.existingSecret` setting
also works with authenticated bundled Redis without disabling `budget-redis`.

## External PostgreSQL for Temporal

Provision `temporal` and `temporal_visibility` databases and a user that can manage their
schemas. Back up and restore both databases before switching an existing installation;
Helm does not migrate them. The old bundled PostgreSQL claim remains when it is disabled.

```bash
read -rs -p 'PostgreSQL password: ' POSTGRES_PASSWORD; echo
printf '%s' "$POSTGRES_PASSWORD" | kubectl create secret generic agents-postgres -n aw --from-file=password=/dev/stdin
unset POSTGRES_PASSWORD
cat > postgres-values.yaml <<'YAML'
workflows:
  postgres:
    enabled: false
  temporal:
    schema:
      useHelmHooks: true
    server:
      config:
        persistence:
          datastores:
            default:
              sql: &externalSql
                connectAddr: postgres.example.com:5432
                user: temporal
                databaseName: temporal
                existingSecret: agents-postgres
                secretKey: password
                createDatabase: false
                manageSchema: true
                tls:
                  enabled: true
                  enableHostVerification: true
                  serverName: postgres.example.com
            visibility:
              sql:
                <<: *externalSql
                databaseName: temporal_visibility
YAML
helm upgrade aw deploy/charts/agentworkflows -n aw --reuse-values -f postgres-values.yaml --wait --timeout 15m
```

The connection and existing Secret are supplied to both Temporal datastores and the schema
Job; no bundled PostgreSQL Service, StatefulSet or generated password Secret is rendered.
Use a trusted CA (mount private CAs into both Temporal server and schema-job containers
and set each SQL `tls.caFile` when needed). If your DBA manages schemas, set `manageSchema`
false for both stores after applying the matching Temporal migrations separately.
External database hooks can run before upgrade; the bundled database keeps hooks disabled
so a fresh installation can create PostgreSQL first.

## Upgrade

Back up PostgreSQL, Redis and the bootstrap Secrets together. Preserve the namespace,
release name and credential Secret names. `lookup` reuses existing credentials on
`helm upgrade`; Helm needs permission to read those Secrets. User-owned Secrets are
read without being adopted. Offline `helm template` cannot look up live Secrets and
generates new example credentials on each render; do not apply those over a running release.

Keep your overrides in a values file and review new chart defaults before upgrading:

```bash
helm dependency update deploy/charts/workflows
helm dependency update deploy/charts/agentworkflows
helm upgrade aw deploy/charts/agentworkflows -n aw -f my-values.yaml --wait --timeout 15m
```

To omit the bundled workflow stack, set `workflows.enabled=false`; the gateway then
omits the Temporal readiness check. The console's workflow operations require Temporal
and a worker. For production, review [production readiness](production-readiness.md),
the [Kubernetes production checklist](kubernetes-production-checklist.md), storage backups,
identity and ingress. The umbrella disables the component NetworkPolicies whose rules
describe the separate GitOps namespaces.

## Verify the optional settings on kind

These are caller-run checks; no cluster install is part of chart render validation.
Prepare dependencies as above. Render defaults and each file from the preceding sections:

```bash
helm lint deploy/charts/agentworkflows --strict
helm template aw deploy/charts/agentworkflows -n aw > /tmp/aw-default.yaml
for file in tls-values.yaml ha-values.yaml network-values.yaml redis-values.yaml postgres-values.yaml; do
  helm lint deploy/charts/agentworkflows --strict -f "$file"
  helm template aw deploy/charts/agentworkflows -n aw -f "$file" > "/tmp/aw-$file"
done
helm lint deploy/charts/agentworkflows --strict -f tls-values.yaml -f oidc-values.yaml
helm template aw deploy/charts/agentworkflows -n aw -f tls-values.yaml -f oidc-values.yaml > /tmp/aw-oidc.yaml
python3 -m unittest discover -s scripts/tests -p 'test_umbrella_chart.py' -v
```

For a disposable default smoke test, use the install command at the start of this guide,
check `/readyz`, sign in with the bootstrap key and complete one Research run. Then use
the exact upgrade commands above one feature at a time. TLS needs an installed controller,
a trusted certificate and working host resolution; OIDC needs a real test identity provider.
Check the browser's `aw_session` cookie is Secure, sign in, refresh, log out, and verify
the mapped team and role. With HA, check two ready gateway pods and `ALLOWED DISRUPTIONS=1`;
delete one gateway pod, wait for recovery and verify the existing session/run still works.
Use a multi-node kind cluster to check anti-affinity placement.

Network enforcement needs a NetworkPolicy-capable CNI; kind's default networking alone
does not establish this. With the policy enabled, run these probes (the two denied probes
must time out/fail; the first must succeed):

```bash
kubectl run aw-allowed -n ingress-nginx --rm -i --restart=Never --image=busybox:1.37.0 -- \
  wget -T 5 -qO- http://inference-gateway.aw:8080/readyz
kubectl run aw-denied -n aw --rm -i --restart=Never --image=busybox:1.37.0 -- \
  wget -T 5 -qO- http://inference-gateway:8080/readyz
kubectl run aw-redis-denied -n aw --rm -i --restart=Never --image=busybox:1.37.0 -- \
  nc -z -w 5 budget-redis 6379
```

Also complete a provider-backed Research run (worker, Redis, Temporal and tool traffic),
exercise OIDC under the policy, and confirm an unlisted destination fails from a gateway
pod. Test external Redis and PostgreSQL in a **fresh namespace with restored/test data**
and the same Secret names, using their values files; confirm `/readyz`, sign-in, a complete
run, and persistence after restarting gateway/Temporal pods. No bundled Redis resources
or PostgreSQL Service/StatefulSet should exist there. Finally render and install the
combined configuration with all six `-f` files, using reachable endpoints and reviewed
CIDRs. Create the referenced Secrets in `aw-production-smoke` first, then run:

```bash
helm lint deploy/charts/agentworkflows --strict \
  -f tls-values.yaml -f oidc-values.yaml -f ha-values.yaml \
  -f network-values.yaml -f redis-values.yaml -f postgres-values.yaml
helm template aw deploy/charts/agentworkflows -n aw-production-smoke \
  -f tls-values.yaml -f oidc-values.yaml -f ha-values.yaml \
  -f network-values.yaml -f redis-values.yaml -f postgres-values.yaml > /tmp/aw-production.yaml
helm install aw deploy/charts/agentworkflows -n aw-production-smoke --create-namespace \
  -f tls-values.yaml -f oidc-values.yaml -f ha-values.yaml \
  -f network-values.yaml -f redis-values.yaml -f postgres-values.yaml --wait --timeout 15m
```

Rehearse the checklist's upgrade/rollback and restore steps on that installation.

## Uninstall

```bash
helm uninstall aw -n aw
```

PostgreSQL's StatefulSet claim and user-created provider Secrets remain. Helm-managed
credentials and Redis's chart-managed claim are removed, so save backups first. For a
disposable namespace, remove all remaining data and stop its port-forward:

```bash
kubectl delete namespace aw
# If you created a dedicated test cluster:
kind delete cluster --name aw
```

Do not reuse an old PostgreSQL claim with a newly generated password: restore its matching
`temporal-postgres-auth` Secret or delete the old claim before a fresh evaluation install.
