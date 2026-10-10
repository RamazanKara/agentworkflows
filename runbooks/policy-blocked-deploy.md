# Incident Runbook: Policy Blocked Deploy

## Symptoms

Argo CD sync fails with an admission error from Kyverno.

## Inspect

    kubectl get events --all-namespaces --field-selector reason=PolicyViolation
    kubectl -n argocd describe application security-policies
    kubectl get clusterpolicy
    kyverno apply deploy/policies/kyverno/policies.yaml --resource <rendered-resource.yaml>

## Likely Causes

The workload is missing required labels, uses `latest`, lacks CPU or memory requests and limits, runs as root, or references an unsigned project image.

## Mitigation

Fix the manifest. Keep policy enforced, and make any exception time-boxed, documented, and reviewed. Reserve audit mode for rolling out new signature verification rules.

## Evidence

Record the blocked manifest, Kyverno error message, Argo CD sync status, and corrected manifest diff.

