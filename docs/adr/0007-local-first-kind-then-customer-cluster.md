# 0007. Local-first kind, then customer cluster

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

The platform targets two audiences with one repository: a contributor or evaluator who needs to run
the whole stack on a laptop in minutes, and a customer who runs it on their own production Kubernetes.
One deployment model for both makes the local lab evidence for the customer path. The decision is
how to structure environments so the local experience is genuinely the same
operating model as the customer one (same charts, GitOps layout, policies, runbooks, and evidence)
on any provider.

## Decision

Make `kind` the local-first environment and the customer cluster a parallel overlay of the same
repository, differing only in cluster directory, pinned revision, and the platform services the
customer already operates.

- The local cluster is a single-node `kind` config,
  [`deploy/clusters/local/kind-config.yaml`](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/clusters/local/kind-config.yaml) (pinned
  `kindest/node` image, `ingress-ready` and `platform.ai/node-pool=local` node labels, host port
  mapping), brought up by `make local-up`.
- Both environments use the same charts and the same Argo CD app-of-apps mechanism
  (see [0001](0001-gitops-with-argo-cd.md)); the only structural difference is the cluster directory
  each root app points at ([`deploy/clusters/local`](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/clusters/local) versus
  [`deploy/clusters/customer`](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/clusters/customer)) and a pinned `targetRevision` on the
  customer root.
- The customer overlay runs on an existing Kubernetes cluster and adds what a tenant cluster needs:
  `appprojects.yaml` for project tenancy, `external-secrets.yaml`, `gpu-scheduling.yaml`, and GPU
  value profiles. It is generated/wired through `make customer-overlay` with the customer repo URL,
  revision, and GPU profile (per the README).
- The README states the boundary plainly: the local lab runs fully on `kind`, and customer clusters
  "replace only the platform services they already operate: ingress, storage classes, secret
  backends, logging, observability, and GPU node pools."

## Consequences

- The local lab is a faithful rehearsal of the customer deployment: a contributor exercises the same
  gateway, policies, governance, and evidence path locally that a customer runs in production, so
  local validation is meaningful as handoff evidence.
- Differences are isolated to a cluster directory and a few overlay manifests, which keeps the diff
  between "works on my laptop" and "works on the customer cluster" small and reviewable.
- The platform runs on the customer's cluster and integrates with its platform services; the operator
  brings ingress, storage classes, secrets, logging, observability, and GPU pools. Delivery is a
  "controlled handoff" into that environment.
- Maintainers keep the two cluster directories in lockstep; the shared charts and policies keep that
  work small, and reviewers compare local and customer values when either changes.

## Alternatives considered

- **A managed local cluster (minikube, k3d, Docker Desktop Kubernetes).** Any could host the local
  lab. `kind` was chosen for its CI-friendliness (it is the conformance test harness's own cluster),
  reproducible pinned node image, and minimal host footprint.
- **Cloud dev cluster as the primary local environment.** `kind` was preferred because it keeps the
  entry experience provider-neutral, runs on a laptop, and makes the first run fast.
- **Cloud-specific Terraform / provider modules as the deployment unit.** The default follows the
  decision-guide's stated position: provider-neutral GitOps and Helm surfaces. A customer can wrap
  the cluster provisioning in their own IaC; the platform's deliverable starts at "Kubernetes already
  exists."
- **One environment only (local-only, or customer-only).** Two environments were chosen because
  together they give production evidence and a fast, GPU-free path for contributors and evaluators.
