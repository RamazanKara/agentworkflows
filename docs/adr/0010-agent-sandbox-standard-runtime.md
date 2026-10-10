# 0010. Agent-sandbox is the standard workspace runtime

- Status: Accepted
- Date: 2026-07-02
- Deciders: Ramazan Kara (maintainer)

## Context

ADR 0009 adopted kubernetes-sigs/agent-sandbox as an *optional, profile-gated*
workspace runtime behind a `sandbox.runtime` toggle, defaulting to the older
namespace-only path. The rest of the platform is deliberately opinionated: Kyverno policies ship in `Enforce`,
namespaces are PSA-`restricted`, egress is default-deny, and images must be
signed. The workload that executes model-generated code, the very reason this
platform has a threat model, belongs on the strongest default isolation, and a
single path keeps the test matrix, the documentation, and the adopter's
decisions simple.

The controller installs from vendored, checksummed manifests in seconds on any
conformant cluster (confirmed on cgroup-v1 `kind`); only the kernel-isolation
*runtime class* (gVisor/Kata) is environment-dependent, and that stays a
separate, optional knob. Making the change early puts every adopter on one
path from the start.

Two adjacent duplications had accumulated around the same feature:

- `make agent-lab-up` installed the workspace with a manual Helm release,
  overlapping the Argo CD-managed `agent-workspace` Application on resource
  ownership on GitOps-managed clusters.
- `make sandbox-smoke` (the request-tracing check for the `ai-sandbox`
  namespace) shared a name with the new agent-sandbox runtime smoke while
  testing request tracing; the Argo Application for that construct is
  called `traceable-sandbox`.

## Decision

1. **The hardened agent-sandbox runtime is the only workspace runtime.** The
   `sandbox.runtime` toggle is removed; `deploy/charts/agent-workspace`
   always renders the hardened `Sandbox`. `sandbox.runtimeClassName` remains
   the only environment-dependent option. This amends decision points 1 and 6
   of ADR 0009; everything else in ADR 0009 stands.
2. **The short-lived projected workspace credential is on by default**
   (`workspace.credentials.projectedToken.enabled: true`), consistent with
   AgentWorkflows' short-lived-credentials stance.
3. **The controller is a platform prerequisite**, installed as an
   `agent-sandbox-controller` Argo CD Application (server-side apply, early
   sync wave) in both cluster overlays, and by `make agent-sandbox-install`
   in the quickstart path.
4. **`make agent-lab-up` is removed.** GitOps owns the workspace instance;
   bare-cluster and demo installs use a documented one-line `helm upgrade
   --install`. `make agent-smoke` validates the deployed instance, and
   `make agent-sandbox-smoke` is validation-only for the same reason.
5. **`make sandbox-smoke` is renamed `make trace-smoke`** (script included)
   so "sandbox" unambiguously means the workspace runtime, matching the
   `traceable-sandbox` Application name.

## Consequences

- One runtime path: half the chart test matrix, a single decision-guide path,
  and a default posture that equals the documented posture.
- Workspace clusters run the vendored controller, which installs on any
  conformant cluster; the tracing and tenant features run independently of it.
- `C-ISOLATE` is mandated at every risk tier, and evidence packs require the
  controller to be present.
- Existing local labs run one Argo sync (the controller Application) or
  `make agent-sandbox-install` before the workspace Application converges.
- The long-lived sandbox pod holds the RWO workspace PVC, and the
  `agent-smoke` Job shares it on the same node. On multi-node clusters,
  schedule them together or use RWX storage.

## Alternatives considered

- **Deprecation window (flip default, keep fallback one release).** Switching
  directly was chosen because, at this stage, it keeps a single test matrix
  from the first release.
- **Keep the toggle indefinitely.** A single path was chosen so the default
  matches AgentWorkflows' security posture.
- **Also collapse the `platform` umbrella chart into GitOps-only.** OCI chart
  distribution stays, as a deliberate, separately recorded decision
  (ADR 0008) about how the platform is consumed.
