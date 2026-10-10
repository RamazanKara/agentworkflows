# Security overview

This page summarizes the security-relevant defaults in release `v0.9.0`. The [threat model](threat-model.md) has the detailed trust boundaries and controls. The [production readiness matrix](production-readiness.md) lists validation commands.

## Defaults that matter

- The local profile uses the public key `local-development-only` for the local lab.
- In-cluster application traffic is plaintext HTTP. NetworkPolicy controls reachability.
- The output guardrail and stored Responses state are off in the base chart and enabled per deployment.
- The local RAG profile turns tenant isolation off because it serves one shared demo corpus.
- A tenant-bound key record or verified JWT claim binds `X-Sandbox-ID` to the caller's identity.
- The agent-sandbox pod template is restricted. Select an isolation `RuntimeClass` such as gVisor or Kata in the values to add a separate kernel boundary.
- The bundled Redis, Qdrant, and Loki footprints are single-node reference services.
- Generate current security evidence for your deployment; sample reports under `results/` show the report format.

Review and adapt the customer values before you deploy them.

## Gateway controls

The gateway supports API-key hashes and JWT/JWKS verification. Key records can add scopes, expiry, sandbox binding, and budget overrides. JWT configuration can bind a verified tenant claim to the sandbox. A contradictory `X-Sandbox-ID` is rejected when a binding exists.

Before forwarding an inference request, the gateway can enforce:

- allowed model IDs and routing policy;
- tenant/request classification: confidential and restricted data stay on local routes;
- message, prompt, tool, completion, and batch size limits;
- per-sandbox rate and estimated-token budgets;
- input credential-pattern detection;
- a per-process concurrency limit and load shedding.

The optional output guardrail can flag, redact, or block configured credential, PII, and denied-content patterns. Redact and block apply to non-streaming responses. Streaming responses are flagged after the stream is emitted.

These are deterministic application checks. Pair them with tool approvals, least-privilege credentials, and egress controls for prompt-injection defense.

## Tenant and RAG identity

The customer RAG values enable owner-based tenant filtering, which acts as a security boundary when the tenant identity is trustworthy.

The RAG service can verify its own JWT and derive the tenant from a claim. With JWT verification off, it uses `X-Sandbox-ID` as supplied by the caller. For multi-tenant use, put direct RAG access behind a trusted identity-stamping path or enable RAG-side JWT verification.

Tenant NetworkPolicies default to deny and add explicit DNS, gateway, RAG, and reviewed external CIDR rules. The cluster CNI enforces them. The local cluster uses Calico by default so that NetworkPolicy is enforced.

## Workspace isolation

Agent workspaces use the vendored `kubernetes-sigs/agent-sandbox` controller and a restricted pod template: non-root user, read-only root filesystem, dropped capabilities, ambient service-account token disabled, resource limits, namespace RBAC, and default-deny egress.

The projected platform token is short-lived and audience-bound. Treat it as a credential. Keep the egress catalog narrow and treat files, retrieved text, model output, and tool arguments as untrusted.

## Audit records

The gateway audit event stores request metadata and hashes in place of raw prompt or completion text. Records are linked into a per-process hash chain.

Cloud calls use the same controls and record the selected provider, routing attempts,
classification, usage, and estimated cost on that chain. Classification refusals are
explicit 403 receipts. Provider credentials come only from server environment variables
or Kubernetes Secret references, never caller headers or bodies. See
[cloud route configuration](model-selection.md#cloud-routes-milestone-1) for egress,
credential rotation, and protocol boundaries.

The chain detects edits and reordering in an exported sequence. For deletion and completeness evidence across replicas and log streams, export logs, retain the `chain_id`, and commit chain-head anchors to a separate trusted system. Use `make audit-verify` and follow the [audit-chain runbook](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/audit-chain.md).

Review runtime, ingress, proxy, RAG, object-store, and application logs as well, since each component manages its own logging.

## Supply chain

The tag-only release workflow builds the first-party images, signs image and chart digests with Cosign, and attaches SDK checksums. SBOM and vulnerability scans run locally with `make supply-chain-check` and `make image-scan`. GitHub Actions are pinned by commit.

Verify the integrity and license of customer model weights, customer base images, external Helm charts, private mirrors, and the target cluster through your own process. Forks that publish their own images update the Kyverno image reference and signing identity so admission accepts them.

## Production checklist for operators

- connect gateway and RAG auth to the customer identity boundary;
- source secrets from the customer secret system;
- enable transport encryption where required;
- select and test a kernel-isolation runtime for higher-risk agent workspaces;
- replace bundled stateful services with an appropriate availability and backup design;
- configure log export, retention, chain-head anchoring, alerts, and incident response;
- validate model artifacts, RAG ingestion, tenant binding, and egress rules;
- generate current eval, load, restore, policy, and supply-chain evidence.

## Related documents

- [Threat model](threat-model.md)
- [OWASP Top 10 for LLM Applications 2025 mapping](owasp-llm-top-10-mapping.md)
- [AI governance crosswalk](ai-governance-crosswalk.md)
- [Security policy](https://github.com/RamazanKara/agentworkflows/blob/main/SECURITY.md)
- [External stores](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/external-managed-stores.md)
- [Guardrails](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/guardrails.md)
