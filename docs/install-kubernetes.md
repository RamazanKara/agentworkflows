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

## Enable OIDC

Follow [Company sign-in (OIDC)](workflows.md#company-sign-in-oidc) for registration,
claims and Secret configuration. Nest its Helm values under `inference-gateway.auth.oidc`.
Set `teamClaim` to a claim containing `default`, configure the exact HTTPS callback URL,
and set `inference-gateway.adminConsole.cookieSecure=true` for HTTPS. The umbrella uses
`false` for localhost port-forwarding; switch it before exposing the console through TLS ingress.

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
storage backups, identity and ingress. The umbrella disables the component NetworkPolicies
whose rules describe the separate GitOps namespaces.

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
