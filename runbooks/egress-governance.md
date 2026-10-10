# Egress Governance Runbook

Use this runbook when adding external network access for coding-agent workspaces or tenant labs.

## Policy

Agent and tenant namespaces use default-deny networking. Egress to the inference gateway and RAG service is built in. Any external CIDR must be approved in `platform/network/egress-catalog.yaml` and referenced from the tenant or workspace values with `catalogRef`.

## Add An External Destination

1. Add or update an approved catalog entry in `platform/network/egress-catalog.yaml`.
2. Set the entry `status` to `approved`, define the owner, environments, expiry date, use cases, data classification, CIDRs, and ports.
3. Reference the entry from `tenants/onboarding/<tenant>.yaml` or `deploy/clusters/<environment>/values/agent-workspace.yaml`:

       allowedEgressCidrs:
         - catalogRef: customer-git-artifact-mirror-example
           cidr: 203.0.113.0/24
           ports: [443]
           description: customer-approved Git, artifact, or package mirror example

4. Run:

       make egress-check

5. Generate evidence:

       make egress-report

## Review Rules

Keep external egress narrow:

- Prefer customer-controlled mirrors over broad internet access.
- Use TLS ports unless a reviewed exception exists.
- Use one catalog entry per trust boundary.
- Keep `expiresOn` current and review entries before renewal.
- Use specific destination CIDRs for coding-agent workspaces in place of `0.0.0.0/0` or broad private ranges.

## Expiry in the cluster

`make egress-check` validates `expiresOn` at review time, and the expiry also travels with the
policy into the cluster, where Kyverno and a CronJob act on it. Each `allowedEgressCidrs` entry must carry
`expiresOn` (required at render time, and cross-checked against its catalog entry), and the
chart stamps it onto the rendered NetworkPolicy:

    metadata:
      labels:
        platform.ai/egress-governed: "true"
      annotations:
        platform.ai/egress-expires-on: "2027-05-31"
        platform.ai/egress-catalog-refs: customer-git-artifact-mirror-example

Two controls read it:

- **Kyverno** (`ai-platform-restrict-egress-cidrs`) requires the expiry annotation on every
  governed exception and denies one whose date has passed. The rule also runs in background
  scan, so an exception that lapses while applied is reported ahead of the next admission.
- **A CronJob** in the workspace namespace (`networkPolicy.expiryEnforcement`) checks the
  annotation daily. It is **report-only by default**: it fails the job and logs that traffic
  is still allowed. Set `removeExpired: true` to have it delete the approved-egress policy;
  default-deny then stays in force, so the workspace keeps platform-internal traffic and
  fails closed for everything else.

Report-only is the default because deleting the policy cuts that egress for whatever depended
on it. Turn enforcement on once you are alerting on the job's failure. The CronJob receives the
`delete` permission only when `removeExpired` is true.

## Troubleshooting

If `make egress-check` fails, compare the requested `cidr`, `ports`, and environment against the catalog entry. The validator requires an approved, non-expired catalog entry whose destination exactly matches the requested network and ports, and it requires the reference's own `expiresOn` to match the catalog's.
