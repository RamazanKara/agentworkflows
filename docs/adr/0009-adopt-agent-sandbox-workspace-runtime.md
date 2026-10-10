# 0009. Adopt kubernetes-sigs/agent-sandbox as the coding-agent workspace runtime

- Status: Accepted
- Date: 2026-07-02
- Deciders: Ramazan Kara (maintainer)

## Context

The coding-agent workspaces pillar isolates agent workloads at the namespace
level: `deploy/charts/agent-workspace` provisions a Pod Security
Admission-restricted namespace with default-deny NetworkPolicies, catalog-gated
egress (`platform/network/egress-catalog.yaml`), ResourceQuota, LimitRange,
least-privilege RBAC, and PVC-backed storage. The platform-level sandbox is a
logical construct: a validated `X-Sandbox-ID` that the inference gateway binds
to budgets, metrics, and tamper-evident audit events
(`src/inference-gateway/app/main.py`).

This gives strong *tenant* isolation. An agent that executes model-generated
code shares the node kernel with other workloads, behind the container runtime
and the restricted pod security profile. *Kernel* isolation is the next layer.

An upstream primitive for it is available. `kubernetes-sigs/agent-sandbox` (SIG Apps) reached
v0.5.0 in June 2026 and graduated its core API to `agents.x-k8s.io/v1beta1`
(`Sandbox`), with `SandboxTemplate`, `SandboxClaim`, and `SandboxWarmPool`
under `extensions.agents.x-k8s.io/v1beta1`. It provides secure-by-default
networking on the template path (cluster-internal IPs and metadata endpoints
blocked since v0.2.1; a directly created `Sandbox` relies on the cluster's own
NetworkPolicies, confirmed on `kind`, 2026-07-01), lifetime bounds
(`shutdownTime`/`shutdownPolicy` on sandboxes, `ttlSecondsAfterFinished` on
pooled claims), suspend/resume, warm pools for ~1 s allocation, and Python/Go
SDKs. It supports stronger runtimes (gVisor, Kata Containers) via the pod
runtime class. It is installed from versioned release manifests. Managed
offerings and production users exist.

Adopting the upstream primitive lets AgentWorkflows focus on its differentiation, the
governance envelope around agent execution: approved-egress catalog, per-sandbox
budgets, the audit chain and evidence packs, and the control-framework map
(`platform/governance/control-framework-map.yaml`).

## Decision

Adopt kubernetes-sigs/agent-sandbox as an **optional, profile-gated workspace
runtime** underneath the existing governance envelope:

1. `deploy/charts/agent-workspace` gains a `sandbox.runtime` value:
   `namespace` (current behaviour, default, works on any conformant cluster)
   or `agent-sandbox` (renders a hardened `Sandbox` with an inline pod
   template on the installed agent-sandbox controller).
2. The hardened profile sets `runtimeClassName` (gVisor or Kata where the
   cluster provides it, mirroring the optional `accelerator.runtimeClassName`
   pattern in the vLLM chart), disables service-account token automount,
   and stays compatible with the `restricted` Pod Security level.
3. AgentWorkflows' NetworkPolicies and `ApprovedEgressCatalog` remain the
   **authoritative egress control** and govern the direct-`Sandbox` path. The
   catalog is the single allow-path, `make egress-check` extends to
   sandbox-managed pods, and policy-as-code enforces that sandbox pods are
   selected by the default-deny policy.
4. The gateway's logical sandbox binds 1:1 to the runtime sandbox: the
   validated `X-Sandbox-ID` equals the `Sandbox` resource name and is carried
   on its labels (`platform.ai/sandbox-id`), so budgets, metrics, and audit
   events correlate with the workload that produced them.
5. Evidence packs (`make evidence`) gain an agent-sandbox readiness check
   (controller present, template renders, runtime class available), and the
   control-framework map gains an isolation control (working id `C-ISOLATE`):
   recommended at the `medium` risk tier, mandated at `high`.
6. Profiles: the local `kind` lab keeps `sandbox.runtime: namespace` as the
   default (laptops run without gVisor); customer GPU and
   regulated-offline profiles document and default to `agent-sandbox`.

## Consequences

- Kernel-level isolation is available for agent code and tied to governance
  risk tiers.
- SIG Apps and its ecosystem (SDKs, warm pools, managed equivalents) maintain
  the isolation primitive.
- AgentWorkflows provides governance, egress, budgets, and evidence as the
  layer above the standard agent-sandbox primitive.
- The upstream warm-pool model supports low-latency workspace allocation
  without bespoke pooling code.
- The platform builds on the `v1beta1` API. Release manifests are vendored
  and pinned in `docs/version-matrix.md`, and upgrades go through the normal
  release-verification path.
- The controller installs from vendored versioned manifests alongside the
  Helm/Argo CD deployment.
- gVisor/Kata are documented cluster prerequisites for the hardened profile
  (decision guide + version matrix). Hardened-profile CI runs in an
  environment that provides them; the local lab uses the default runtime.
- `make validate` renders and tests both runtime paths (namespace,
  agent-sandbox).
- The egress catalog stays the single source of truth for every sandbox path,
  with AgentWorkflows' default-deny policies governing upstream's
  template-scoped NetworkPolicy model.

## Alternatives considered

- **Namespace-only isolation.** Kept as the default and local-lab profile at
  this stage so the envelope runs on any conformant cluster. agent-sandbox adds
  the syscall-level barrier the `high` risk tier calls for.
- **A project-owned sandbox CRD and controller.** The SIG Apps project already
  has production adoption, and building on it keeps the "governance layer over
  standard primitives" positioning.
- **`runtimeClassName` on plain workspace pods.** A minimal dependency-free
  hardening step. agent-sandbox was preferred because it adds lifecycle
  semantics (TTL, suspend/resume, claims, warm pools) and ecosystem
  compatibility.
- **Hosted sandbox services.** In-cluster execution was preferred because it
  keeps agent workloads inside the customer's own boundary, matching the
  self-hosted scope (`docs/scope-and-non-goals.md`).
