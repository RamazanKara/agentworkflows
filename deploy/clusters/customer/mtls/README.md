# Encryption in Transit (Opt-In Overlay)

By default AgentWorkflows' in-cluster data plane uses **plain HTTP**, and NetworkPolicies restrict
*who* may connect (default-deny plus explicit allows). The payloads that traverse the pod network
(prompts, completions, retrieved RAG context, and the `X-API-Key` header) are the data a regulated
deployment protects in transit. Encrypting the data plane is an operator responsibility, and this
overlay gives two reviewed, opt-in ways to do it. Pick one of them.

See [docs/threat-model.md](../../../../docs/threat-model.md) (Transport confidentiality) and the
"Encryption in transit" row of [docs/production-readiness.md](../../../../docs/production-readiness.md).

## Option A: Service mesh mTLS (recommended)

If the cluster runs (or can run) a mesh, this is the lowest-friction path: the mesh issues and
rotates workload certificates and encrypts pod-to-pod traffic transparently; services stay as they are.

- Label the platform namespaces for sidecar injection (mesh-specific), then apply
  [`istio-peerauthentication-strict.yaml`](istio-peerauthentication-strict.yaml) to require STRICT
  mTLS in each platform namespace. Linkerd is equivalent: annotate the namespaces for injection and
  mTLS is on by default.
- Cilium (sidecar-free) is another option: enable WireGuard or IPsec transparent encryption at the
  CNI, which encrypts all node-to-node pod traffic cluster-wide.

## Option B: cert-manager-issued TLS

For clusters that standardize on cert-manager, terminate TLS in each service behind a cert-manager-issued certificate.
[`cert-manager-selfsigned.yaml`](cert-manager-selfsigned.yaml) ships a self-signed `ClusterIssuer`
and an example `Certificate` for the gateway; replace the self-signed issuer with your enterprise CA
(or an ACME issuer) and mount the resulting secret into the gateway/RAG pods, fronting them with a
TLS listener.

## Scope

This overlay is applied deliberately per environment, separately from the default GitOps
apps. The certificate authority, mesh installation, and secret material are customer-owned.
