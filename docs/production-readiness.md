# Production Readiness Matrix

AgentWorkflows is cloud-first. The local trial demonstrates its governed provider path; production deployments must supply identity, persistence, and current evidence. The Kubernetes profiles are optional deployment templates. Customer clusters should keep the same interfaces and replace only the platform services they already operate, such as ingress, storage, secrets, logging, and GPU node pools.

## Required Controls

| Area | Local lab implementation | Customer cluster expectation | Validation |
| --- | --- | --- | --- |
| Runtime isolation | Ollama in `ollama`, optional vLLM in `vllm`, gateway in `inference` | Dedicated namespaces and runtime service accounts | `make smoke RUNTIME_BACKEND=ollama` |
| Accelerator portability | vLLM profiles for CPU-off local lab, NVIDIA, and AMD ROCm | Customer clusters expose `nvidia.com/gpu` or `amd.com/gpu` | `make production-check` |
| Runtime high availability | Gateway replicas, vLLM replicas, HPA, PDBs, and topology spread | Size min/max replicas to SLOs and GPU inventory | `helm template` in `make validate` |
| Traceability | `X-Request-ID`, `X-Sandbox-ID`, optional `traceparent`, JSON audit events | Forward the same headers through ingress and log pipeline | `make smoke`, `make trace-smoke`, gateway tests |
| API authentication | Gateway and RAG business endpoints require API keys in local/customer values | Back hashes with customer secret manager and rotate keys through External Secrets | gateway/RAG auth tests, `make smoke`, `make rag-smoke` |
| API contracts | `platform/api-contracts/` stores OpenAPI snapshots for gateway and RAG with stable operation IDs and auth declarations | Review contract diffs before changing customer-facing routes, request schemas, or auth semantics | `make api-contract`, `make api-contract-update` |
| Configuration contracts | `platform/config-contracts/` stores service runtime env snapshots and checks them against Python settings, Helm templates, and chart defaults | Review config diffs before changing customer overlays, secrets, budgets, retrieval settings, or runtime endpoints | `make config-contract`, `make config-contract-update` |
| Prompt privacy | Audit logs include prompt length and SHA-256 only | Keep raw prompt text out of logs by default | `test_audit_log_redacts_prompt_content` |
| Data retention | `platform/governance/data-retention.yaml` covers audit logs, generated evidence, RAG knowledge, agent workspace data, and model governance records | Align retention days and classification to customer policy | `make retention-check`, `make retention-report` |
| Model governance | Gateway `ALLOWED_MODELS` rejects unapproved model IDs | Maintain an approved model catalog per environment | `test_chat_completion_rejects_disallowed_model` |
| Model catalog | `platform/model-catalog/models.yaml` and cluster ConfigMap | Treat model additions as reviewed changes | `make production-check` |
| Model lifecycle | Approved models require promotion requests, evidence references, runtime metadata, and approved-only allowlists | Review promotion requests before adding models to gateway allowlists | `make model-check`, `make model-report` |
| Model provenance | Approved Hugging Face models use pinned commits and safetensors checksum inventories; Ollama records registry weight-layer digests | Verify downloaded weights against the inventory or registry digest before production use; record customer model-store changes in provenance | `make model-provenance-check`, `make model-provenance-verify`, `make model-provenance-report` |
| Admission control | Gateway caps message count, prompt size, completion tokens, temperature, and streaming | Tune limits by sandbox and runtime capacity | `test_admission_policy_rejects_unsafe_or_expensive_requests` |
| Prompt secret detection | Gateway rejects prompts that match configured credential patterns before runtime forwarding | Keep enabled for coding-agent workspaces and tune patterns through review | gateway guardrail tests, `make production-check` |
| Output guardrail | Gateway inspects model completions for leaked credentials/PII/blocked content and flags, redacts, or blocks before return (OWASP LLM02:2025/LLM05:2025) | Enable `guardrails.outputGuardrail`; use non-streaming for hard redact/block enforcement | gateway output-guardrail tests |
| Per-tenant RAG isolation | Retrieval is scoped to the caller's tenant via the `owner` payload field stamped at ingest; enforced by default on both backends and fail-closed | Keep `retrieval.tenantIsolation` enabled for multi-tenant corpora (chart local single-tenant profile turns it off); stamp each source `owner` with the tenant id; front the service with the gateway or a header-stamping proxy since the tenant id is header-asserted | RAG tenant-isolation tests |
| Validation toolchain | `platform/tools/validation-toolchain.yaml` declares `validate`, `local`, and `strict` profiles with a pinned Linux/CI installer | Install the strict profile before customer handoff or production-readiness sign-off | `make toolchain-install`, `make toolchain-doctor`, `make validate-full` |
| SLO and error budget | `platform/slo/objectives.yaml` defines inference, eval, restore, and coding-agent platform objectives with alert references | Align targets to the customer's contract and review burn-rate alerts | `make slo-check`, `make slo-report` |
| Sandbox budgets | Gateway enforces request, prompt-character, and estimated-token ceilings by `X-Sandbox-ID` | Size limits by tenant and review overage events | `test_sandbox_budget_status_and_request_limit_rejection` |
| Shared budget backend | Local/customer values use Redis-compatible shared counters for multi-replica gateways; budgets fail closed on a Redis outage while the rate limiter can opt into fail-open (`rateLimit.failOpen`, default closed) | Replace bundled Redis with external managed/Sentinel/Cluster Redis ([runbook](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/external-managed-stores.md)); choose the rate-limit fail policy per availability target | `test_redis_budget_tracker_shares_usage_across_tracker_instances`, `test_rate_limit_fail_open_admits_when_backend_down` |
| Quota and chargeback | `platform/governance/quota-plans.yaml` connects tenant quotas, gateway budgets, workspace sizing, and chargeback labels | Align quota plans to customer showback or chargeback policy before onboarding tenants | `make quota-check`, `make quota-report` |
| Sandbox isolation | `ai-sandbox` namespace, quota, limits, default-deny network policy | Per-team sandbox namespaces with quotas and egress allowlists | `make trace-smoke` |
| Tenant labs | `make tenant-up` and `make tenant-smoke` create team namespaces with quota, RBAC, trace contract, and network controls | One namespace per team or approved experiment boundary | `make tenant-smoke` |
| Tenant onboarding | `TenantOnboarding` spec renders tenant controls and matching agent workspace values | Review generated quota, RBAC, PVC, storage, and egress before apply | `make tenant-onboard`, `scripts/tenant-onboard.py --check` |
| Regulated offline tenant profile | `tenants/onboarding/regulated-offline-coding-agents.yaml` renders confidential agent controls with in-cluster egress only | Use for offline or regulated teams and add egress only through reviewed catalog-backed changes | `make tenant-onboard-regulated`, `make production-check` |
| RAG service | Local retrieval service returns platform context and OpenAI-compatible grounded messages | Replace or extend the knowledge set with customer-approved internal docs | `make rag-smoke`, RAG service tests |
| Vector RAG profile | `deploy/charts/qdrant-vector-store` and customer RAG values provide a persistent Qdrant backend | Size storage, vector dimensions, and ingestion to the customer's embedding model and approved document pipeline | `make production-check`, Qdrant/RAG Helm renders |
| Agent workspaces | `agent-workspace` chart creates a locked-down namespace, PVC, RBAC, trace contract, and approved egress for coding agents | One workspace per team, project, or agent boundary with customer-approved external egress | `make agent-smoke` |
| Egress governance | `platform/network/egress-catalog.yaml` requires external agent egress to reference approved catalog entries | Review and expire Git, package mirror, artifact, and ticketing egress entries | `make egress-check`, `make egress-report` |
| Chaos drills | Safe rollout drills for gateway, budget Redis, Ollama, RAG, Qdrant, vLLM, and GPU capacity preflight | Run after platform upgrades and before customer demos or maintenance windows | `make chaos-drill`, `DRILL=gpu-capacity-preflight RUN_SMOKE=0 make chaos-drill` |
| Evaluation harness | `platform/evals/smoke-suite.yaml`, `platform/evals/coding-agent-suite.yaml`, and `make eval` for repeatable prompt and coding-agent checks | Maintain environment-specific suites and keep summaries as release evidence | `scripts/eval-suite.py --check-config` |
| Adversarial safety eval | `platform/evals/safety-suite.yaml` red-team battery (prompt-injection, jailbreak, data-exfiltration, unsafe-tool-use, and bias/fairness cases) gated by a `safety` release gate (minRefusalRate over a minCases floor) | Extend the red-team suite per model and require it before promotion | `SUITE=platform/evals/safety-suite.yaml make eval`, `make release-gate` |
| RAG grounding eval | `scripts/rag-eval.py` scores retrieval plus RAGAS-style context precision and answer faithfulness | Add ground-truth answers; gate on `minFaithfulness`/`minContextPrecision` | `make rag-eval-check` |
| Model quality drift | Proxy alerts (`governance.rules`) plus scheduled eval comparison | Schedule evals and alert routing; roll back on threshold breach | [runbooks/model-drift-monitoring.md](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/model-drift-monitoring.md) |
| Release gates | `platform/slo/release-gates.yaml` enforces eval, load, restore, strict toolchain, SLO, governance, supply-chain, and evidence-pack thresholds | Run strict gates before demos, releases, restore reviews, and production-readiness handoff so they run against fresh evidence | `make release-gate`, `make release-gate-strict`, `make release-report-strict` |
| Autoscaling | KEDA ScaledObject from Prometheus request rate | Tune thresholds to customer SLOs and GPU capacity | `helm template` in `make validate` |
| Observability | Prometheus metrics, Grafana dashboards, Loki + Promtail log pipeline | Centralize metrics, logs, and alerts | `make validate`, dashboards in `deploy/observability/` |
| Distributed tracing | Gateway/RAG export OTLP spans to a Tempo backend wired as a Grafana datasource | Set `observability.tracing` endpoint; size Tempo retention/storage | `make dashboard-check`, `deploy/observability/applications.yaml` |
| Cost and FinOps | OpenCost app, gateway `estimated_cost_usd` metric + `/v1/usage`, cost dashboards | Map cost-center labels to chargeback; review cost dashboards | `make dashboard-check`, cost panels in inference/opencost dashboards |
| Encryption in transit | HTTP data plane by default with an opt-in mTLS/cert-manager overlay | Enable a CNI/mesh mTLS or cert-manager TLS control before regulated multi-tenant use | `deploy/clusters/customer/mtls/` |
| Runtime threat detection | Optional Falco/Tetragon detective layer (opt-in Argo app) | Deploy into a Kyverno-excluded namespace; route alerts to the log pipeline | `deploy/observability/runtime-security.yaml`, runbook |
| Policy as code | Kyverno required labels, resources, pod hardening, read-only root filesystems, image signature audit | Enforce on AI namespaces and exclude platform operators | `make policy-test` when Kyverno CLI is installed |
| Cost controls | Required owner/cost/environment/sandbox labels and OpenCost app | Map labels to chargeback/showback taxonomy | `make validate` YAML checks |
| Secret handling | External Secrets examples; runtime tokens stay out of the repository | Replace local Kubernetes provider with enterprise backend | `deploy/clusters/customer/external-secrets.yaml` |
| Supply chain | Pinned Alpine runtime images, hashed Python dependency locks, runtime-only Python dependencies, high/critical Trivy image and repo failure gates, local SBOM/SARIF/checksum evidence, Cosign digest signing and release asset upload in the tag-only release workflow | Deploy only immutable signed image digests that you have scanned | `make dependency-lock-check`, `make repo-security-scan`, `make image-scan`, `make supply-chain-check`, GitHub Actions release workflow |
| Backup and restore | `restore-drill` application-data validation and Velero examples | Run scheduled restore evidence for each critical data store | `make restore-drill`, `make backup-drill` |
| Disaster recovery | Single-cluster DR posture with named RPO/RTO and a whole-platform restore order | Provision an off-cluster backup target; design secondary-cluster/multi-region if required | [runbooks/disaster-recovery.md](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/disaster-recovery.md) |
| Model cards | Each approved model ships a card/datasheet referenced from the catalog | Keep cards current with promotion; treat as a review artifact | `make model-check` |
| Load testing | k6 chat-completion scenario with sandbox tags, live-gateway mode, and self-contained local gateway-path mode | Store summaries and compare against SLOs | `make loadtest`, `make loadtest-local` |
| Evidence pack | Static customer handoff report plus optional live Kubernetes readiness checks | Attach reports to release, demo, restore drill, or incident review evidence | `make evidence`, `make evidence LIVE=1` |

## Stateful stores: dev/reference footprints and their HA path

Four bundled stateful stores ship as **single-node reference footprints** so a laptop lab and
a fresh cluster start without external dependencies. Swap each to its external/HA path for
production, regulated or multi-tenant use. The full opt-in procedure
(with rollback) is in the [external / managed stores runbook](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/external-managed-stores.md).

| Bundled store | Reference footprint | Production / HA path |
| --- | --- | --- |
| Temporal PostgreSQL (`deploy/charts/workflows`) | 1 replica and a persistent volume for history and visibility | Configure both Temporal SQL datastores for managed HA Postgres; back up both databases and validate restore before switching traffic |
| Budget / response-cache Redis (`deploy/charts/budget-redis`) | 1 replica, AOF and PVC persistence, `minAvailable: 0` (budgets fail closed during an outage) | Point `budget.redisUrl` / `responseCache.redisUrl` at an external **managed Redis, Redis Sentinel failover pair, or Redis Cluster** and stop syncing the bundled Application. Budgets stay fail-closed on outage; the rate limiter can opt into fail-open (`rateLimit.failOpen`) as an availability-vs-enforcement tradeoff |
| Qdrant vector store (`deploy/charts/qdrant-vector-store`) | Single-instance, **schema-enforced** (`replicaCount` max 1) on one RWO PVC | Use an **external managed Qdrant or a Qdrant cluster** (sharded/replicated) and point `retrieval.vectorStore.url` at it |
| Loki (`deploy/observability/applications.yaml`) | `SingleBinary`, `replication_factor: 1`, filesystem storage | Move to a **scalable/distributed Loki mode with object storage and replication**; forward the tamper-evident audit receipts onward to a SIEM for durable long-term hold |

The bundled charts stay available as working references, so rolling back to the reference
footprint for a demo is a one-line values change.

## Workflow upgrades and recovery

By default the 0.9.0 gateway uses **Redis** for run indexes, step timelines and
team/run budgets. Optional [PostgreSQL gateway storage](postgresql-storage.md) moves
run metadata, receipts, audit events, team settings and key metadata to a separate gateway
database; Redis continues to hold live budgets and the other state listed there.
Temporal owns workflow history in `temporal` and SQL visibility in
`temporal_visibility`, both on the existing PostgreSQL service. Back up all three stores
(plus the gateway database if you enable PostgreSQL storage) together with the complete
receipt export.

Gateway startup applies numbered Lua migrations from `app/migrations` atomically with
the schema marker in the existing budget Redis. Migration 001 adopts the unversioned
0.2.0 keys without rewriting counters, run IDs, receipts or expiry times. Repeated and
concurrent startups are safe. Startup proceeds on a known schema version; to roll back
across an incompatible future migration, restore a pre-upgrade backup.

Temporal uses its upstream versioned SQL migrations for **both** databases. Compose pins
server/auto-setup to 1.29.1; the existing Helm dependency uses server/admin-tools 1.32.0,
now explicitly pinned together. These are separate upgrade tracks: move a Compose database
through each server minor in turn. Follow the
[Temporal upgrade sequence](https://docs.temporal.io/self-hosted-guide/upgrade-server),
apply schema updates before rolling servers, and keep `numHistoryShards` fixed for an
existing database. The chart explicitly declares the visibility store and schema management.

For the first Helm install, `temporal.schema.useHelmHooks=false` lets the bundled Postgres
start before the schema Job. For upgrades on an existing release, run:

```sh
helm dependency build deploy/charts/workflows
helm upgrade workflows deploy/charts/workflows -n workflows -f your-workflow-values.yaml \
  --set temporal.schema.useHelmHooks=true --wait --wait-for-jobs --timeout 10m
```

Keep the same release, namespace, database names, secrets and PVCs. For GitOps, order the
schema Job before the server rollout using the controller's sync phases. Keep old worker
workflow code compatible with retained histories; this migration keeps histories as written.

Run the isolated fixture drills (Docker, Git and Python 3.12+; they run without provider credentials):

```sh
make workflow-upgrade-test
make workflow-restore-drill
make workflow-helm-upgrade-test  # also requires kind, Helm and kubectl
```

On Windows, the first two entry points also run directly in PowerShell as
`python scripts/workflow-recovery.py upgrade` and `python scripts/workflow-recovery.py drill`.
The Helm test uses native Helm/kubectl and the gateway's Python environment (PyYAML),
with kind and Docker in WSL Ubuntu.

The upgrade drill builds the 0.2.0 release commit `f17850aa91091c306921db577c18c15df7da3ef0`,
starts a run and waits for approval, then replaces images while retaining volumes. It checks
all retained gateway values, both SQL schema versions, run listing, receipt IDs and budgets,
then approves the original execution. The restore drill dumps both Postgres databases,
snapshots Redis, destroys only its own disposable volumes, restores and resumes that run.
Both verify the receipt chain and terminate a worker during an active model call, checking
that the call completes once. Reports and backups go under `.out/aw-hardening-*`; all drill
containers and volumes are removed even on failure. Builds are sequential, workers have
two activity slots, and Windows invokes Docker through `wsl.exe -d Ubuntu -e docker`.
The Helm test creates a separate kind cluster, caps its node at two CPUs and 4 GiB, installs
the previous charts/images, then upgrades the same releases to two gateway and worker
replicas. It checks retained PVC identities, run state, budgets and receipts, resumes the
waiting run and deletes the cluster. It uses an isolated kubeconfig, local fixtures and
temporary loopback ports. This single-node drill tests upgrades.
Seeding the old Helm release supplies the required `connectProtocol: tcp` operator override
for both SQL stores; current chart defaults include it. The database contents and previous
application images remain the 0.2.0 baseline.

For your Compose trial, take a quiesced backup and restore into a **new** project:

```sh
python scripts/workflow-recovery.py backup --project agentworkflows \
  --directory .out/workflow-backup --receipts /path/to/complete-retained-receipts.jsonl
# Stop the source before reusing its published ports, or select unused Compose ports.
python scripts/workflow-recovery.py restore --project agentworkflows-restored \
  --directory .out/workflow-backup
```

Omit `--receipts` only for a first container lifetime, before any receipts exist. Backup stops
workers, drains the gateway, stops Temporal, then takes `pg_dump -Fc` of each database and
a Redis RDB snapshot; it restarts the source afterward. Restore writes into empty Temporal
tables and an empty Redis. It restores the archived receipts alongside the backup and
verifies them with `audit-verify --anchor --strict-continuity`, including new restart links.
Checksum or chain errors fail before database writes. Copy the backup and its manifest/head
anchors to separately controlled storage so the anchors can authenticate the backup. Retain
policy files, credentials and exact deployment values through your existing
secret/configuration backup process. A completed backup defines the recovery point. Measure recovery time on your own data volume.

For Helm/managed stores, use the same maintenance order and database-native `pg_dump -Fc`
and `pg_restore --create --exit-on-error` for **both** Temporal databases, plus a persistence
snapshot of every gateway Redis database and the SIEM receipt export. Restore to isolated
databases first, verify schema versions and receipts with the included verifier, point a
test gateway/worker at those stores, and resume a waiting run before switching traffic.
The Compose drill is a reproducible application recovery test.

## Multiple replicas and shutdown

Merge the chart's `values-ha.yaml` after your reviewed deployment values:

```sh
helm upgrade inference-gateway deploy/charts/inference-gateway -n inference \
  -f your-gateway-values.yaml -f deploy/charts/inference-gateway/values-ha.yaml
helm upgrade workflows deploy/charts/workflows -n workflows \
  -f your-workflow-values.yaml -f deploy/charts/workflows/values-ha.yaml \
  --set temporal.schema.useHelmHooks=true --wait --wait-for-jobs --timeout 10m
```

The gateway and its bundled console run in two replicas; the gateway serves the console
directly and keeps sessions in the shared store, so replicas stay in sync. The worker has two replicas on the same team
task queue. Each deployment keeps at least one pod available during voluntary disruptions
and rolls with zero unavailable pods. Topology spread distributes pods when nodes permit.
KEDA's minimum is two in the gateway HA values. Temporal's example uses three replicas
per server role with PDBs keeping two. Use enough nodes to realize those guarantees.
The bundled Postgres and Redis are single-node references. For highly available storage,
point the existing store settings at your managed HA stores and configure persistence,
backups and networking there.

`/healthz` checks the running event loop and intentionally stays live during dependency
outages. Gateway `/readyz` checks required Redis stores, local runtimes, cloud credential configuration and
Temporal when `workflows.temporalAddress`/`TEMPORAL_ADDRESS` is set. Worker port 8081
`/readyz` requires an active worker, Temporal and gateway readiness; `/healthz` checks
its event loop. Dependency outages withdraw readiness without causing restart storms.

SIGTERM stops worker polling and lets active activities finish for 180 seconds before
Temporal's shutdown cancellation. Pods and Compose allow 210 seconds; the gateway drains
requests for 180 seconds, with a five-second Kubernetes pre-stop delay for endpoint removal.
Set the pod grace above your longest custom activity and SDK shutdown timeout when extending
the supplied three-minute activity limit. After a forced kill or exhausted grace, Temporal
retains the step and retries the activity. Tools must honor their idempotency key so a
retried call applies its external side effects once; container steps run once, without
automatic retry. Retain every replica's audit stream and external head anchors to prove
completeness across concurrent replica lifetimes.

## Live providers and concurrent runs

`make test` excludes the paid acceptance directory. Acceptance runs execute when
`AGENTWORKFLOWS_LIVE_PROVIDERS=1` is set and `CI` is unset. Each provider runs when its own
real environment credential is present.
The suite covers chat, streamed content and usage, forced tool calls, a deterministic
primary outage followed by a real-provider fallback, and receipt/provider cost accounting.
Keep credentials in the environment, out of tracked files and command arguments.

| Provider | Credential variable | Configuration prefix |
| --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `LIVE_OPENAI` |
| Anthropic | `ANTHROPIC_API_KEY` | `LIVE_ANTHROPIC` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` | `LIVE_AZURE_OPENAI` |
| Bedrock bearer-token API | `AWS_BEARER_TOKEN_BEDROCK` | `LIVE_BEDROCK` |
| Vertex OpenAI-compatible endpoint | `VERTEX_ACCESS_TOKEN` | `LIVE_VERTEX` |

For each enabled provider set `<PREFIX>_MODEL`, `<PREFIX>_INPUT_USD_PER_1K`, and
`<PREFIX>_OUTPUT_USD_PER_1K` to the approved model and current prices. Set
`<PREFIX>_BASE_URL` for Azure, Bedrock and Vertex; OpenAI and Anthropic use their standard
API URLs. Choose models supporting tools and reported streaming usage. Cost assertions
use configured prices. With your environment already provisioned:

```sh
AGENTWORKFLOWS_LIVE_PROVIDERS=1 make test-live-providers
```

The default test gate checks the same adapters with local protocol fixtures.

With the fake Compose trial running, `make workflow-loadtest` submits 20 workflow runs at
concurrency two, checks their results, receipt IDs and per-run budgets, and asserts the
aggregate team spend is exactly $0.220 in synthetic configured prices. It writes throughput,
median/p95 latency and failures to `results/loadtest/workflow-runs.json`. This test checks
concurrent accounting and completion.

Local result on 2026-10-07 (Windows client, WSL Docker, fake Compose providers, one worker
with two activity slots): **20/20 completed**, 0 failures, 3.476 seconds total, 5.754 runs/s,
0.337 s median and 0.353 s p95 end-to-end latency. All 20 receipt IDs and run budgets
verified; shared spend increased by $0.220.

## Promotion Review

Use the matrix above as the source of truth, then review this shorter sequence before a customer handoff or production-style demo:

- Run `make validate-full`, `make api-contract`, `make config-contract`, and `make release-gate-strict` against current evidence.
- Confirm auth, prompt redaction, model allowlists, sandbox budgets, quota labels, and NetworkPolicies match the target environment.
- Review tenant onboarding output before applying it; regulated/offline tenants must keep external CIDR egress disabled.
- Verify RAG knowledge, vector-store dimensions, GPU resource names, runtime replicas, HPA/PDB settings, and topology spread against customer capacity.
- Run smoke, RAG, agent, eval, load, restore, and chaos evidence paths that are relevant to the handoff.
- Confirm image scan, SBOM, checksum, signature, and repo security evidence exists for the images being promoted.
- Generate an Evidence pack and attach the Markdown report to the handoff notes; retain JSON evidence with the release or drill record.
