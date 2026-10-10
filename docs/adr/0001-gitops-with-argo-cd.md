# 0001. GitOps delivery with Argo CD

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

The platform must deliver the same platform (gateway, runtimes, RAG, vector store, budget Redis, agent
workspaces, policies, observability, backup) onto a local `kind` cluster and onto customer-owned
clusters, with no manual `kubectl apply` drift between them. Delivery has to be declarative so the
desired state is reviewable in Git, auditable for customer handoff, and reproducible by an operator
who has never seen the cluster.

The README states the model directly: the local lab runs fully on `kind`, and customer clusters
"keep the same repo structure and replace only the platform services they already operate." That
requires a continuous-reconciliation tool that reads manifests and Helm charts straight from this
repository.

## Decision

Use Argo CD with an app-of-apps layout.

- A single root `Application` per environment points at a cluster directory and includes only its
  app list: [`deploy/gitops/argocd/root-app.yaml`](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/gitops/argocd/root-app.yaml)
  targets `deploy/clusters/local` and includes `apps.yaml`;
  [`deploy/gitops/argocd/root-app-customer.yaml`](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/gitops/argocd/root-app-customer.yaml)
  targets `deploy/clusters/customer` and includes `{apps.yaml,appprojects.yaml}`.
- [`deploy/clusters/local/apps.yaml`](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/clusters/local/apps.yaml) declares the child
  `Application`s: model-catalog, traceable-sandbox, platform-operators (Kyverno, KEDA,
  External Secrets via their upstream Helm charts), observability, security-policies, the Ollama and
  vLLM runtimes, budget-redis, inference-gateway, qdrant-vector-store, rag-service, agent-workspace,
  OpenCost cost-controls, and the Velero/restore-drill backup apps.
- Every child app sets `syncPolicy.automated` with `prune: true` and `selfHeal: true`, so manual
  edits are reverted and removed manifests are pruned.
- The local root tracks `targetRevision: HEAD`; the customer root pins a tagged revision (for
  example `v0.11.0` in the committed example) so customers reconcile against a fixed, reviewed
  release.
- Bootstrapped through `make bootstrap-argocd` and reconciled with `make sync`, matching the
  README's local run path.

## Consequences

- One reconciliation engine drives both environments; local and customer share the same deploy
  mechanism and differ by cluster directory and pinned revision.
- Self-heal plus prune means the cluster converges to Git, which is what the customer-handoff and
  evidence story depends on: the repository is the auditable source of desired state.
- The operator installs and operates Argo CD, which is bootstrapped separately from the application
  charts. For a fast workstation check, `QUICKSTART_DIRECT_APPLY=1 make quickstart` does a direct
  Helm apply.
- The app-of-apps pattern (root app -> app list -> workloads) keeps the per-environment surface to a
  single root manifest.

## Alternatives considered

- **Flux.** A capable GitOps controller with a strong Helm and image-automation story. Argo CD was
  chosen for its app-of-apps ergonomics, its first-class `AppProject` tenancy boundary (used in the
  customer overlay's `appprojects.yaml`), and a UI that helps during customer handoff and demos.
- **Direct `helm`/`kubectl` apply, scripted in CI or Make.** Simplest to start, and the platform keeps
  this path for the `QUICKSTART_DIRECT_APPLY=1` workstation check. Argo CD is the primary model
  because it continuously reconciles, prunes removed resources, and self-heals manual drift, all of
  which the customer-handoff evidence story relies on.
- **Cloud-provider GitOps / Terraform-driven delivery.** Argo CD was preferred as the default
  because it keeps the platform provider-neutral. The decision-guide's explicit position is "provider-neutral
  GitOps and Helm surfaces instead of cloud-specific Terraform."
