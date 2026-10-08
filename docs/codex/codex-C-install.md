Repository: RamazanKara/agentworkflows, branch main. Pull first; commit and push straight to main when green. Read docs/PRODUCT-GAPS.md (gap 3). Other v0.5.0 changes may land on main while you work; rebase, don't revert them.

Goal: `helm install` gives a working AgentWorkflows (gateway + console, Redis, Temporal + PostgreSQL, worker) in one namespace with one command, the cloud-first way.

1. deploy/charts/agentworkflows (umbrella):
   - Add the `workflows` chart (deploy/charts/workflows) as a dependency with condition `workflows.enabled`, default true.
   - Defaults for a cloud-first product: gateway, budget-redis and workflows on; ollama, vllm, rag-service and qdrant off (opt-in).
   - Wire everything inside the release namespace: gateway `workflows.temporalAddress` → the release's Temporal frontend; worker `gatewayUrl` → `http://inference-gateway:8080`.
   - Generate missing secrets when not provided (`temporal-postgres-auth` password, the worker's gateway key, a bootstrap admin key) with `lookup` so upgrades keep them; print in NOTES.txt how to read the admin key (`kubectl get secret ... | base64 -d`) and how to port-forward the console.
   - Ship a minimal default sandbox policy and model routing with one team (`default`), a `research` example workflow and cloud-provider routes that need only a provider key Secret (`helm install ... --set providers.openai.existingSecret=...`). Without a provider key the install still comes up healthy and the console says which secret to add.
   - Keep the GitOps (deploy/clusters/*) path unchanged.

2. Verify for real on the laptop with kind (Docker Desktop is fine): `helm dependency update`, `helm lint`, `helm install aw deploy/charts/agentworkflows -n aw --create-namespace --wait --timeout 15m` using the v0.4.0 GHCR images, all pods Ready, port-forward the gateway, `GET /readyz` returns ready including Temporal, sign in to /console with the bootstrap admin key, start the research workflow through `agentworkflows runs start` against a fake or real provider and approve it. Save the command transcript. Then `helm uninstall` and delete the kind cluster (keep the laptop clean: stop containers when done).

3. Add docs/install-kubernetes.md: prerequisites, the one command, reading the admin key, adding a provider key, enabling OIDC (link to the workflows guide), upgrading, uninstalling. Link it from README.md "Deploy and operate" and docs/index.md. Update CHANGELOG.md under "v0.5.0 - unreleased".

4. `make lint` and `make test-gateway` stay green. If the repo has chart render tests (scripts/ or tests/ referencing helm template), update them.

Report: what changed, the kind verification transcript summary, anything not verified.

Also verify the Compose quickstart now that deploy/compose/compose.yaml pulls the published GHCR images (no local build): from a fresh `git clone` of main, run the docs/quickstart.md Bash steps exactly as written except clone main instead of the tag, through approval and `python scripts/audit-verify.py receipts.log`. The cloud sandbox could resolve the manifests but was blocked from downloading the image layers, so this has not been run yet. Then `docker compose down -v`.
