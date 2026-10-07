# Quickstart

Try AgentWorkflows in ten minutes: discover models, send a governed call, inspect your
team's usage, run a durable workflow, and verify its receipts. Version 0.2.0 adds projects,
roles, verified approvals, and shared spend controls to the cloud gateway.

## Docker Compose

Install Docker with Compose, Git, Make, Bash, Python 3.12+, and curl. Run from a Bash shell
(Linux, macOS, or WSL). No GPU, model download, provider key, or paid inference is needed.

```bash
git clone https://github.com/RamazanKara/agentworkflows.git
cd agentworkflows
make compose-up
make compose-smoke
```

The images build one at a time. The gateway listens on `127.0.0.1:8080`, and retrieval on
`127.0.0.1:8090`. The bundled `cloud-fake` service supplies synthetic responses and prices
for all five provider protocols. This trial tests routing, streaming, budgets, secret
blocking, confidential-data refusal, reported agent actions, and audit verification.
It also kills and replaces a Temporal worker during a research workflow, then verifies
approval, publication, and no repeated completed model calls. It does not validate a live
provider account or measure model quality.

The walkthrough ends with `All checks passed` after deliberately editing a receipt and
checking that verification rejects it. The original log is `.out/compose/gateway-audit.jsonl`.
Open <http://127.0.0.1:8080/console> and enter `local-development-only` to inspect health,
models, usage, and budgets. This public demo key is bound to sandbox `demo`; do not reuse it
outside the trial.

Temporal history and approval signals are visible at <http://127.0.0.1:8233>. Follow the
[workflow guide](workflows.md) to start a run, read its draft, approve or reject it, and
inspect its token/cost budget. Temporal uses its own PostgreSQL; run budgets use persistent
Redis. Both survive ordinary container restarts.

## Make your first call

```bash
python -m pip install ./sdk/python
export AGENTWORKFLOWS_API_KEY=local-development-only
agentworkflows models
agentworkflows chat "Hello, AgentWorkflows!" --model demo-openai
agentworkflows usage
```

`agentworkflows --help` lists the commands. `AGENTWORKFLOWS_URL` defaults to
`http://127.0.0.1:8080`; set it when using a remote gateway. The Python import is
`from agentworkflows import GatewayClient`. Existing OpenAI and Anthropic clients can use
the same gateway; follow the [client examples](client-examples.md).

Follow [team setup](workflows.md#teams-projects-and-roles) to issue builder/approver/viewer
credentials, add projects, map provider keys, and set shared token/USD limits.
`agentworkflows team` discovers your access; `agentworkflows runs --help` lists run commands.
The console is read-only. Usage uses configured prices and conservative reservations.

## Connect a real provider

Use the [cloud route example](model-selection.md#cloud-routes-milestone-1) for one approved
provider and model. It includes the connection, pricing, and server-side credential mapping.

1. Replace fixture routes with approved provider URLs, model IDs, and contracted token prices.
2. Supply the named credential through the gateway's environment or Kubernetes Secret.
   Never put provider keys in client requests or tracked files.
3. Set the team's `providerCredentials` mapping to those environment variable names and add
   the provider origin to workflow `allowedEgress`. Bind gateway keys to roles/projects.
   Review data classification:
   confidential requests cannot use a cloud route, even as a fallback.
4. Restart the gateway, list models, and make one authorized call. Cloud readiness checks
   credential presence only; a successful real call is required to validate account access.

Use [production readiness](production-readiness.md) before serving a team outside the trial.
The Compose fixtures and public key are evaluation defaults.

## Optional self-hosted models

To add the small CPU Ollama model to the existing trial:

```bash
docker compose -f deploy/compose/compose.yaml --profile self-hosted up -d ollama
docker compose -f deploy/compose/compose.yaml --profile self-hosted run --rm model-pull
```

Append this item under `spec.models` in `deploy/compose/model-routing.yaml`:

```yaml
    - id: qwen2.5:0.5b
      backend: ollama
      pricing:
        inputUsdPer1kTokens: 0
        outputUsdPer1kTokens: 0
```

Then load the policy and call the model:

```bash
docker compose -f deploy/compose/compose.yaml restart inference-gateway
agentworkflows chat "Hello from my local model" --model qwen2.5:0.5b
```

The pull downloads model weights and caches them in a Docker volume. vLLM and hardened
agent workspaces are available through the Kubernetes lab below.

## Troubleshooting and cleanup

| Symptom | Action |
| --- | --- |
| Cannot connect | Run `make compose-up`; check `docker compose -f deploy/compose/compose.yaml ps` and gateway logs |
| Port already in use | Stop your previous trial or set `AGENTWORKFLOWS_GATEWAY_PORT` / `AGENTWORKFLOWS_RAG_PORT`; set `AGENTWORKFLOWS_URL` to the chosen gateway port for the CLI |
| 401 | Set `AGENTWORKFLOWS_API_KEY=local-development-only` for the demo; use your team's key on a real gateway |
| Model refused | Run `agentworkflows models` and choose a listed ID |
| 403 classification refusal | Use an approved self-hosted route for confidential data; keep the classification policy intact |
| 429 budget or rate limit | Inspect `agentworkflows usage`; ask the team administrator to review the budget or wait for the rate window |
| No real generated answer | `demo-*` models are protocol fixtures; connect an approved provider or add Ollama above |

An optional chat UI is available with
`docker compose -f deploy/compose/compose.yaml --profile ui up -d` at
<http://127.0.0.1:3000>. It talks to the governed gateway.

Stop the stack and remove its volumes:

```bash
make compose-down
```

Compose does not install Kubernetes network policies, hardened workspaces, GitOps, or
production availability controls. The local lab below evaluates Kubernetes deployment.

## Local Kubernetes lab

The lab creates a single-node `kind` cluster, builds the two first-party images, deploys the
platform through Argo CD, and runs gateway and RAG smoke tests against `qwen2.5:0.5b` on Ollama.
It uses plaintext in-cluster HTTP, single-node data stores, and workstation storage.

### Requirements

The managed bootstrap supports Linux and WSL and requires:

- Docker with a working daemon;
- Python 3.12 or newer;
- Bash, `curl`, `tar`, `sha256sum`, and `install`;
- enough disk for the `kind` node, platform images, and the Ollama model.

The bootstrap downloads pinned copies of `kind`, `kubectl`, Helm, kubeconform, the Kyverno CLI,
k6, Syft, the Argo CD CLI, Cosign, and Trivy into `.tools/bin`.

The first run also downloads container base images, the Kubernetes node image, Calico and Argo
CD manifests, third-party charts and images, Python packages, and the Ollama model. It creates
Docker state, writes a `kind` context to your kubeconfig, and reserves host port `8080` for the
cluster unless overridden. Stop the Compose stack first (`make compose-down`) if it is running.

On macOS or a managed workstation, install `kind`, `kubectl`, and Helm separately and run
`make quickstart`; the repository's tool installer is Linux-only.

### Run it

```bash
make bootstrap
```

If the required cluster tools are already installed:

```bash
make quickstart
```

The command runs, in order:

1. the local toolchain check;
2. `make validate`;
3. `make local-up` to create the cluster and build the gateway and RAG images;
4. `make agent-sandbox-install` from the vendored manifests;
5. Argo CD bootstrap and sync;
6. the gateway smoke test against Ollama;
7. the RAG smoke test against the local lexical corpus.

The Argo CD path needs the configured Git repository to be reachable from the cluster. For a
reduced workstation check that applies the core charts directly, use:

```bash
QUICKSTART_DIRECT_APPLY=1 make quickstart
```

Direct apply skips the Argo CD application set, including the full observability, policy,
cost, and backup add-ons. It is useful for the gateway and RAG smoke path, not as a GitOps or
production-readiness test.

Other switches:

```bash
QUICKSTART_INSTALL_TOOLS=1 make quickstart  # install the pinned CLI set first
QUICKSTART_SKIP_VALIDATE=1 make quickstart  # skip static validation
QUICKSTART_SKIP_RAG=1 make quickstart       # skip the RAG smoke test
```

### Check the result

A complete default run ends with:

```text
[agentworkflows] smoke test completed for ollama
[agentworkflows] RAG smoke completed for agent-lab
[agentworkflows] quickstart completed
```

These lines confirm that the gateway reached Ollama and that the RAG service returned results.
They do not validate a customer identity provider, GPU runtime, production storage, backup, or
external observability system.

The lab gateway is a ClusterIP service behind a default-deny network policy. Reach it from
your workstation with a port-forward:

```bash
kubectl -n inference port-forward svc/inference-gateway-inference-gateway 18080:8080
curl -s http://127.0.0.1:18080/v1/models -H 'X-API-Key: local-development-only'
```

Then run the hardened agent workspace demo and any focused checks you need:

```bash
make agent-sandbox-demo
make status
make trace-smoke
make tenant-smoke
make agent-smoke
make agent-sandbox-smoke
make evidence LIVE=1
```

### Troubleshooting

Docker must be reachable with `docker info`. If cluster creation stopped partway through,
remove the cluster with `make local-down` before retrying.

The default node image is set in `scripts/local-up.sh` and
`deploy/clusters/local/kind-config.yaml`. Docker hosts using cgroup v1 automatically fall back
to `kindest/node:v1.31.4`. To select a node image explicitly:

```bash
LOCAL_KIND_NODE_IMAGE=kindest/node:v1.31.4 make quickstart
```

If host port `8080` is taken, reserve another one for the cluster:

```bash
LOCAL_GATEWAY_HOST_PORT=18081 make quickstart
```

The smoke scripts use temporary local port-forwards. Override `LOCAL_PORT` for an individual
smoke command if its default port is occupied.

For model-pull progress:

```bash
kubectl -n ollama logs statefulset/ollama
```

### Remove the lab

```bash
make local-down
```

This deletes the `kind` cluster. It does not remove downloaded tools, Docker images, or caches.
`make clean-all` removes repository-local tool environments and generated files; Docker cleanup
remains a Docker operation.

Continue with [Getting started](getting-started.md) for focused validation and
customer-deployment commands.
