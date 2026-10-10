# 0008. Helm packaging and OCI distribution

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

The platform's workloads (gateway, runtimes, RAG, vector store, budget Redis, agent workspaces)
need a packaging format that is parameterizable per environment (local versus customer, Ollama versus
vLLM, NVIDIA versus AMD), reconcilable by Argo CD, and distributable as verifiable, versioned
artifacts a customer can pull and check before installing into a production cluster. The artifacts
must fit AgentWorkflows' supply-chain story (pinned digests, SBOMs, Cosign signatures) using the
existing image registry.

## Decision

Package every first-party workload as a Helm chart and distribute the charts as Cosign-signed OCI
artifacts in the same registry as the images.

- Each workload is a Helm chart under [`deploy/charts/`](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/charts): inference-gateway,
  ollama, vllm, rag-service, qdrant-vector-store, budget-redis, and agent-workspace, all `apiVersion:
  v2`, `version: 0.13.0`, with `kubeVersion: ">=1.25.0"`.
- Environment differences are value files on shared charts: Argo CD applications reference per-cluster
  values such as `../../clusters/local/values/inference-gateway.yaml`
  ([`deploy/clusters/local/apps.yaml`](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/clusters/local/apps.yaml)), and the customer
  overlay supplies its own values including the GPU profiles.
- CI packages the charts and pushes them to `oci://ghcr.io/${IMAGE_REPO}/charts` on tagged and
  main-branch releases ([`.github/workflows/ci.yml`](https://github.com/RamazanKara/agentworkflows/blob/main/.github/workflows/ci.yml)), then
  cosign-signs each OCI artifact by digest in the same workflow that signs the images.
- Verification is documented in [`docs/release-verification.md`](../release-verification.md):
  `helm pull oci://$IMAGE_REPO/charts/<chart> --version "${RELEASE#v}"`, then `cosign verify` against
  the keyless `ci.yml` tag identity. Chart OCI tags drop the leading `v` to match the chart `version`,
  while image tags keep it.

## Consequences

- One distribution mechanism covers images and charts: the same registry, the same keyless Cosign
  identity, the same verification command shape. A customer pulls a chart, verifies its signature,
  renders it with their values, and reviews before install.
- OCI distribution reuses GHCR, which already holds the signed images, SBOM, and provenance
  attestations, so charts live in the same secured registry.
- Helm's per-environment values keep local/customer and Ollama/vLLM/NVIDIA/AMD differences as data,
  which is what lets [0003](0003-inference-runtime-vllm-and-ollama.md) and
  [0007](0007-local-first-kind-then-customer-cluster.md) share charts across environments.
- The tag convention (charts strip the leading `v`, images keep it) is spelled out in the
  verification docs so operators use the right form with `helm pull` and `cosign verify`.
- The API and config contract snapshots (`platform/config-contracts`) pin chart configuration
  surfaces, keeping parameterized templates reviewable.

## Alternatives considered

- **A classic Helm HTTP chart repository (`index.yaml` on a static host or ChartMuseum).** Works and
  is widely understood, with its own signing approach (provenance files). OCI was preferred because it
  keeps charts in the registry that already stores and signs the images, unifying the supply-chain
  story in one artifact store.
- **Raw manifests / Kustomize.** Kustomize overlays can express per-environment differences. Helm
  was preferred because AgentWorkflows' variability (model selection, GPU vendor,
  replica/parallelism tuning) is naturally values-driven, Argo CD already drives these as Helm
  sources, and Helm gives a single packaged, versioned, signable artifact to pull and verify.
- **Plain `git`-only delivery.** Argo CD can render charts directly from this repo, which is exactly
  the local path. For customer distribution, a pulled, pinned, signed OCI artifact is verifiable
  out-of-band before it reaches a cluster, which the release-verification flow depends on. In-repo
  rendering and OCI artifacts work side by side.
