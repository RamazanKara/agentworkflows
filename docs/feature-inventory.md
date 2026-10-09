# Feature inventory

This is the source of truth for what the current checkout implements, what is enabled by
default, and what remains operator-owned. “Shipped” means code, configuration, tests, and an
operator path exist in this repository; it does not mean a customer-specific integration is
configured.

Milestones 2–3 add [durable workflows, framework agents, MCP tools, and workflow policy](workflows.md).

| Capability | Status | Default | Verification / boundary |
| --- | --- | --- | --- |
| Durable workflows | Shipped | Compose on; Helm/GitOps chart | Temporal with dedicated Postgres; worker-SIGKILL recovery smoke |
| Gateway durable storage | Shipped | Redis; PostgreSQL opt-in | [PostgreSQL store](postgresql-storage.md) for runs, receipts, terminal snapshots, audit, settings and managed-key metadata; live budgets, sessions and captured content still require Redis |
| Governed tool execution | Shipped | Deny unless configured in team policy | Fixed URLs, DLP, budget reservations, idempotency keys, run/step receipts |
| Framework agent steps | Shipped | Optional SDK framework dependencies | OpenAI, Anthropic, OpenAI Agents SDK, and LangGraph; local-fake Compose proof |
| Team MCP tools | Shipped | Explicit server/tool registration | Streamable HTTP 2025-03-26, argument/output DLP, costs, and receipts |
| Workflow policy | Shipped | Configured under each team | Provider/model/tool/egress allowlists and immutable run budget caps |
| Container agent steps | Shipped | Existing Kubernetes workspace required | Hardened workspace checks, scoped expiring credentials, one attempt, and completion receipts |
| Workflow token/cost budgets | Shipped | 10,000 tokens / $5 per run in SDK | Immutable team-scoped Redis counters; unknown attempts retain reservations |
| Human approvals | Shipped | Signal + query in example | Durable wait with expiry; verified approver identity and production Temporal authorization remain operator-owned |
| OpenAI chat completions | Shipped | On | Gateway tests, OpenAPI contract, local smoke |
| Legacy completions | Shipped | On, non-streaming | Gateway tests; streaming rejected explicitly |
| Embeddings | Shipped | On | Gateway tests; same auth, budget, audit, and model policy |
| Moderations | Shipped | On | Governance taxonomy, not OpenAI harm categories |
| Anthropic Messages | Shipped | On, streaming and non-streaming | Native translation through the governed chat path; streaming obeys the shared `allowStreaming` toggle |
| OpenAI Responses | Shipped | On, synchronous | Function tools with multi-turn tool calls and image inputs; optional state is off by default; background, streaming, and built-in tools remain out of scope |
| Responses server-side state | Shipped | Off | Tenant-scoped memory/Redis store with TTL and delete |
| Synchronous batch fan-out | Shipped | On | Per-item admission/budget/guardrail tests |
| Files + asynchronous Batch API | Shipped | Off | Bounded streaming upload, durable Redis queue with owner-token claims, object-store blobs, streamed and checkpointed processing, replay bound to the running batch and its submitter |
| Python client SDK and CLI | Shipped (typed) | Install `./sdk/python`; command `agentworkflows` | Isolated build/test matrix, checksums, and release artifacts; PyPI Trusted Publishing is optional |
| API-key authentication | Shipped | Local on; chart base off | Hashed keys or scoped/expiring key records |
| JWT/JWKS authentication | Shipped | Customer template on | Issuer/audience/time/algorithm validation and tenant binding |
| Model allowlist and routing | Shipped | On | Per-model primary/fallback/canary/shadow routes |
| Runtime failover | Shipped | Configured by policy | Readiness accepts a healthy declared fallback chain |
| Prompt and tool-payload admission | Shipped | On | Recursive secret/blocked-term scan and size ceilings |
| Output guardrail | Shipped | Off in base values | Scans visible content and generated tool/function arguments |
| Runtime parameter policy | Shipped | On | OpenAI parameters and reviewed runtime extensions are forwarded; control-defeating extensions (`best_of` > 1, beam search, `chat_template`, `logits_processors`, `priority`, ...) are refused; others are dropped and named in `X-Dropped-Params`; `admission.extraForwardedParams` overrides |
| Dedicated gateway metrics port | Shipped | Chart 9090; Compose uses the API port | `metrics.port`; the API port then answers `/metrics` with 404, and only `networkPolicy.metricsIngressNamespaces` reach the listener |
| Remote image URLs | Shipped | Off (`data:` only) | `admission.imageUrlAllowedHosts` admits named hosts; other schemes are always refused |
| Request/body limits | Shipped | 1 MiB JSON | Files use the independent bounded batch-file ceiling |
| Rate limits and budgets | Shipped | Customer Redis profile on | Atomic shared counters; fixed windows; reservations settled against measured usage; fail policy is explicit |
| Tamper-evident audit receipts | Shipped | On | Redacted fingerprints, chain verifier, head anchors, and chain-of-chains continuity across restarts |
| Agent-action receipts | Shipped | Off | `POST /v1/receipts`; closed action vocabulary, tenant-bound, records claims and enforces nothing (ADR 0014) |
| RAG retrieval receipts | Shipped | On | Own chain, same primitives and same verifier as the gateway |
| Audit chain head persistence | Shipped | Memory (no continuity) | `file` or `redis` head backend, or the opt-in PostgreSQL gateway store, preserves cross-restart linkage; storage is operator-provided |
| Team web console | Shipped | On in Compose/umbrella Helm; opt-in standalone | `/console`; existing auth, identity switching, filtered runs, approvals, step receipts/logs, admin configuration guidance, and costs |
| Docker Compose evaluation stack | Shipped | `make compose-up` | Gateway/console, cloud fakes, Temporal, Redis, worker and RAG on 127.0.0.1; optional Ollama/Open WebUI; headless browser smoke in CI; no Kubernetes network policy or agent workspaces |
| Ollama runtime | Shipped | Local profile | Pinned image; local-only model-pull egress exception |
| vLLM generation runtime | Shipped | Customer profile | NVIDIA/AMD values, explicit task, queue-based autoscaling |
| vLLM embedding runtime | Shipped | Customer profile | Dedicated `--task embed` release consumed by RAG |
| Lexical and Qdrant RAG | Shipped | Lexical local; Qdrant customer | Hybrid retrieval, reranker interface, collection versioning |
| RAG tenant isolation | Shipped | App default on; local shared profile off | Query and document metadata both fail closed by owner |
| Agent sandbox workspace | Shipped | Local/customer profiles | Restricted pod, no ambient token, scoped token, PVC, quotas |
| Network-policy enforcement | Shipped | Calico local default | Reachable-target deny smoke; customer CNI remains operator-owned |
| GitOps delivery | Shipped | Argo CD | Immutable release revisions; every declared app is health-gated and customer sync fails closed |
| Evidence and release gates | Shipped | CI/nightly | Conformance and model-quality evidence are labeled separately |
| Egress exception expiry | Shipped | Report-only | Rendered onto the NetworkPolicy; Kyverno denies expired, CronJob retires them when enforcement is on |
| Signed releases | Shipped | Release workflow | Tag-built multi-arch images and digest-bound charts signed with Cosign; SBOM and scans run locally (`make supply-chain-check`, `make image-scan`) |
| Multi-node model serving | Example/integration | Off | LeaderWorkerSet/Ray installation and topology are operator-owned |
| End-user multi-user chat UI | Example only | Off | Open WebUI manifest/runbook; identity and storage are operator-owned |
| Training, fine-tuning, audio, images | Out of scope | n/a | Use purpose-built systems; see [Scope and non-goals](scope-and-non-goals.md) |

Milestone 1 (0.2.0): OpenAI, Anthropic, Azure OpenAI, Bedrock, and Vertex Gemini
adapters share model policy, budgets, DLP, settlement, and provider/cost receipts. Cloud
routes are opt-in; confidential/restricted tenants and requests remain local. Provider
protocols and the extended Compose walkthrough are tested with local fakes. See
[model selection](model-selection.md#cloud-routes-milestone-1) for configuration and limits.

For operational acceptance criteria, use the [Production readiness matrix](production-readiness.md).
For exact supported versions, use the [Version matrix](version-matrix.md).

## Team operations (since 0.2.0)

Projects and admin/builder/approver/viewer roles extend existing sandbox identities.
The authenticated API/CLI starts, lists, inspects, cancels, retries, and approves runs;
timelines include provider, model, tokens, estimated cost, duration, and receipt IDs.
Team provider-key mappings, shared token/USD budgets, reports, and Grafana dashboards/alerts
are included. Onboarding is declarative; Temporal and workers remain trusted operator
surfaces. Follow the [team walkthrough](workflows.md).
