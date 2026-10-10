# Threat Model

This threat model covers the local lab and customer-owned Kubernetes deployment pattern.

## Assets

- User prompts, completions, RAG queries, and retrieved private context.
- API keys, secret hashes, model-pull credentials, and customer secret-backend references.
- Model artifacts, provenance records, eval results, and release evidence.
- Agent workspace PVC data, tenant manifests, and approved egress policy.
- Gateway audit logs, metrics, and trace identifiers.

## Trust Boundaries

- External callers to gateway and RAG service business endpoints.
- In-cluster platform services and runtime namespaces.
- Tenant and coding-agent namespaces.
- Customer secret manager and External Secrets.
- Model registries, package mirrors, Git hosts, and other approved egress destinations.
- The RAG corpus and retrieved context: document content crosses into the prompt path.
- The build and release pipeline: source, GitHub Actions, the GHCR registry, and signing identity.

## Primary Risks

- Raw prompt or retrieved customer context leaks into logs or evidence.
- Unapproved model IDs bypass the model catalog and gateway allowlist.
- A tenant or agent workspace reaches unapproved network destinations.
- Runtime images or dependencies are promoted with high or critical vulnerabilities.
- Release decisions rely on sample evidence in place of current results.
- Budget exhaustion is reported as a client error in place of capacity/rate exhaustion.

## Current Controls

- API-key hash authentication for business endpoints.
- Prompt and query audit redaction with length and SHA-256 fingerprints.
- Model allowlists, admission limits, prompt secret detection, and sandbox budgets.
- Output-side guardrail: model completions are inspected for leaked credentials/PII/blocked
  content and flagged, redacted, or blocked before return (OWASP LLM02:2025/LLM05:2025).
- Default-deny NetworkPolicies and catalog-backed external egress.
- Hardened agent-sandbox workspace runtime, the standard workspace runtime (ADR 0009,
  ADR 0010): controller-managed sandbox pods with the service-account token disabled, read-only root
  filesystem, all capabilities dropped, a short-lived projected platform credential in place of long-lived
  secrets, and an optional kernel-isolation runtime class, admission-enforced by the Kyverno
  `ai-platform-hardened-sandboxes` policy.
- Pinned runtime images, hashed Python lockfiles, SBOMs, Trivy scans, and Cosign signing.
- Strict release gates that require current evidence, including an adversarial safety/jailbreak
  eval gate (`platform/evals/safety-suite.yaml`).

For a control-by-control mapping to OWASP LLM Top 10 and to NIST AI RMF / EU AI Act / ISO 42001,
see [owasp-llm-top-10-mapping.md](owasp-llm-top-10-mapping.md) and
[ai-governance-crosswalk.md](ai-governance-crosswalk.md).

### Transport confidentiality

The in-cluster data plane is **plaintext HTTP** by default. NetworkPolicies restrict who may
connect, and an operator-owned CNI/mesh control encrypts prompts, completions, retrieved RAG
context, and the API-key header on the pod network. See the opt-in overlay and options in
[deploy/clusters/customer/mtls/README.md](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/clusters/customer/mtls/README.md) (service-mesh
mTLS, Cilium WireGuard/IPsec, or cert-manager-issued TLS). Enable it before
handling regulated data in a multi-tenant cluster (see Required Customer Hardening).

### Detective / runtime monitoring

Admission (Kyverno) and NetworkPolicies are preventive controls. The optional runtime-detection
layer (Falco/Tetragon) observes post-admission behavior of agent and runtime pods. See
[runbooks/runtime-threat-detection.md](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/runtime-threat-detection.md).

### Agent workspace isolation boundary

Coding-agent workspaces execute model-generated code and run on the agent-sandbox runtime
(ADR 0009, ADR 0010): controller-managed sandbox pods on a hardened template with ambient
Kubernetes credentials disabled. The platform credential is a projected, audience-bound
ServiceAccount token with a short TTL that expires on its own and carries no Kubernetes API
access, replacing long-lived secrets. The syscall boundary is the container runtime plus the
restricted pod profile; set `sandbox.runtimeClassName` (gVisor/Kata) where the cluster provides
one, expected at the `high` risk tier (`C-ISOLATE`), to add kernel isolation. Prompt injection and
tool abuse are bounded at the application layer by the egress catalog, budgets, and gateway
guardrails, and the set of approved catalog destinations is a governance decision. The CNI enforces
NetworkPolicy: the local lab defaults to pinned Calico, and `make agent-sandbox-smoke` fails if the
kindnet CNI is present. Its deny target is the Kubernetes API, which is otherwise reachable, so a
refused connection is direct evidence of policy enforcement.

## AI-Specific Threats

These are the threats that distinguish an AI platform from a generic web service.
The controls below are mechanisms in this repo and work together as defense in depth.

### Indirect / RAG prompt injection

- **Primary Risk.** Text in the RAG corpus, a retrieved document, or a
  file in a coding-agent workspace carries instructions that the model follows,
  exfiltrating private context, calling tools, or reaching an attacker-controlled
  destination. The injection rides in on data, so it reaches the model alongside
  an authenticated caller's prompt.
- **Current Controls.** Coding-agent and tenant namespaces run default-deny
  NetworkPolicies, so an agent reaches only approved destinations; external
  egress is allowed only through reviewed `platform/network/egress-catalog.yaml` entries
  referenced by `catalogRef` (broad CIDRs are rejected at render time and by the
  Kyverno `ai-platform-restrict-egress-cidrs` policy). RAG knowledge is mounted
  read-only and ingestion/retention is reviewed per the customer handoff. The
  gateway redacts and fingerprints prompts/queries in place of logging raw text,
  runs prompt secret detection, and applies sandbox budgets and admission limits
  that cap the blast radius of a runaway agent loop. Coding-agent eval suites
  include `forbiddenAny` secret-leak checks. These controls limit what an injected
  instruction can reach. Review which documents enter the corpus and keep agent
  egress narrow.

### Model-artifact tampering / weight poisoning

- **Primary Risk.** A model is swapped, backdoored, or pulled from an
  unverified source, so the served weights differ from the reviewed artifact,
  producing attacker-chosen behavior, leaking data, or degrading evals while
  appearing legitimate.
- **Current Controls.** Only catalog-approved model IDs pass the gateway
  allowlist (`runtime.allowedModels`, cross-checked against
  `platform/model-catalog/models.yaml`); approved entries require a promotion request and
  governed provenance (`platform/governance/model-provenance.yaml` requires `sourceUri`,
  `immutableRef`, `digest`, `license`, `dataClassification`, and `riskTier`),
  verified by `scripts/model-provenance.py` in the strict release gate. Hugging Face
  models pin a commit and safetensors checksum inventory; Ollama entries record
  registry weight-layer digests. Customers verify downloaded bytes before
  production. vLLM/Ollama model caches are isolated per the sandbox and runtime
  security context. Provenance records exactly which artifact was pulled; pair it
  with model evals and upstream source review when selecting weights.

### Build / release pipeline trust boundary

- **Primary Risk.** A compromised GitHub Action, a leaked GHCR push token, or a
  forged keyless signing identity injects a malicious image or chart into the
  release stream, which GitOps then deploys cluster-wide because every
  Application syncs automatically.
- **Current Controls.** All GitHub Actions are pinned to a full commit SHA (in place
  of a floating tag) in `.github/workflows/ci.yml`. Image and chart signing is keyless
  Cosign over OIDC (`id-token: write`), and the Kyverno
  `ai-platform-verify-project-images` policy is set to `Enforce` with the keyless
  `subject` restricted to this repo's CI workflow on `refs/heads/main` and the
  `issuer` to `token.actions.githubusercontent.com`, so admission accepts only images
  signed by that identity. Provenance and SBOM attestations,
  Trivy HIGH/CRITICAL gating, and OpenSSF Scorecard run in CI; releases publish
  supply-chain checksums. Forks that republish update the registry and the
  keyless subject/issuer to their own identity so admission accepts their images.

## Data Residency And PII

Prompts, RAG queries, and retrieved context may contain PII or regulated data.
The platform stores only redacted, length-and-SHA-256 fingerprinted audit
records in place of raw prompt/query text, and pins all data-bearing stores (Qdrant,
agent PVCs) to the customer cluster, so data residency follows wherever the
customer runs the cluster and its backups. Customers classify ingested content,
set retention per `platform/governance/data-retention.yaml`,
and confirm the deployment region meets their residency obligations.

## Required Customer Hardening

- Wire API-key hashes or OIDC/JWT validation to the enterprise identity boundary, with RS256 or ES256 preferred for customer IdPs.
- Enable in-cluster encryption in transit (service-mesh mTLS, Cilium WireGuard/IPsec, or
  cert-manager TLS). The data plane is plaintext HTTP by default. See
  [deploy/clusters/customer/mtls/README.md](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/clusters/customer/mtls/README.md).
- Verify downloaded weights against the pinned inventory or registry digest and record customer model-store changes in provenance.
- Run strict gates with current evidence before production handoff.
- Review RAG document ingestion, retention class, and vector collection access; enable per-tenant
  RAG retrieval isolation (`retrieval.tenantIsolation`) for multi-tenant corpora.
- Deploy the optional runtime-detection layer (Falco/Tetragon) and a DR plan with named RPO/RTO
  ([runbooks/disaster-recovery.md](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/disaster-recovery.md)).
- Align SLO, quota, budget, backup, and incident-response settings to customer policy.
