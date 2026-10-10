# Restricted-egress tenant example

The file `tenants/onboarding/regulated-offline-coding-agents.yaml` defines a tenant named `regulated-offline`. The generated manifests restrict pods in that tenant namespace to in-cluster egress: DNS, the inference gateway and the RAG service.

The profile scopes egress for the tenant namespace. Image pulls, model downloads, GitOps, identity, and services outside the tenant namespace use their own offline controls, listed at the end of this page.

## Render the manifests

```bash
make tenant-onboard-regulated TENANT_OUTPUT=.out/tenants
```

The renderer writes namespace, quota, limit range, NetworkPolicy, RBAC, trace-contract, and agent-workspace values under `.out/tenants`. Review the output before applying it.

The checked-in spec requests:

| Setting | Value |
| --- | --- |
| Data classification | `confidential` |
| External CIDR egress | disabled |
| Allowed in-cluster services | DNS, inference gateway, RAG service |
| Private registry required | `true` metadata flag |
| Evidence retention | 730 days metadata value |
| Job-management RBAC | disabled |

`requirePrivateRegistry` and `evidenceRetentionDays` are contract fields that record the tenant's requirements. Configure the registry, storage lifecycle and retention job to match them.

## Review before apply

Confirm that:

- the target namespace and sandbox ID are correct;
- the CNI enforces Kubernetes NetworkPolicy;
- every generated egress rule targets an in-cluster destination;
- the gateway and RAG namespace selectors match the target cluster;
- the workspace image is available from an internal registry;
- storage class, quota, and PVC size are appropriate;
- identity and API-key material are supplied from a secret store, outside Git.

Apply the reviewed files through the customer's GitOps process. Reserve a direct `kubectl apply` for a disposable lab.

## Test the boundary

Use an image that is already present or available from the internal registry. From a pod in the tenant namespace, test both an allowed in-cluster destination and a destination that is otherwise reachable from the cluster, so the denied result reflects NetworkPolicy.

The repository's agent-sandbox smoke test uses the Kubernetes API as the deny target:

```bash
make agent-sandbox-smoke
```

To make the entire deployment offline, add private image/chart mirrors, preloaded model weights, internal Git and identity endpoints, DNS policy, cluster-wide egress rules, and an offline release-verification process.
