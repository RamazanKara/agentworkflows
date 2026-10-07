# Local evaluation and Kubernetes lab

Start with the [five-minute quickstart](quickstart.md) for an approved workflow.
This guide adds retrieval, self-hosted models, full smoke checks, and the Kubernetes lab.

## Full Compose smoke test

With Bash, Make, Python 3.12+, curl, and Node.js 24/npm installed:

```bash
make compose-up
make compose-smoke
```

This checks all five provider fakes, secret blocking, worker crash recovery, receipts,
and the console in Chromium. Keep real provider overrides disabled for this test.

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
