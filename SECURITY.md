# Security Policy

AgentWorkflows governs cloud-provider access, team budgets, and agent receipts. Treat its gateway, credentials, and deployment configuration as security-sensitive infrastructure.

## Supported Surface

The latest tagged release and `main` receive security fixes. Pre-1.0 older minor lines are not
maintained; operators should upgrade to the latest release. A coordinated fix may be backported
when a customer cannot upgrade immediately, but that is an explicit exception rather than a
standing support promise.

Security fixes should cover:

- inference gateway and RAG service code
- Helm charts and Kubernetes manifests
- GitOps overlays and customer values
- validation, evidence, release, and supply-chain scripts
- model governance, egress, retention, quota, and SLO policy files

## Reporting

Do not open public issues containing secrets, exploit details, customer data, or private prompt content. Report privately through one of these channels:

- **Primary:** GitHub Private Vulnerability Reporting. Open [AgentWorkflows Security](https://github.com/RamazanKara/agentworkflows/security) tab and choose **"Report a vulnerability"**.
  <!-- Maintainer: if "Report a vulnerability" is not available, enable Private Vulnerability Reporting in repo Settings -> Security & analysis. -->
- **Alternate:** email [security@fluentorbit.de](mailto:security@fluentorbit.de).

Include:

- affected component or path
- reproduction steps
- impact and severity
- whether a credential, model artifact, customer document, or generated evidence file is exposed
- suggested mitigation when known

We acknowledge reports within 2 business days and aim to provide a triage update within 7 days.

## Coordinated Disclosure

Please do not publicly disclose a vulnerability until a fix is released. We follow a default 90-day disclosure window and will coordinate timing with you.

## Handling Rules

- Store only API-key hashes in configuration.
- Do not log raw prompts, completions, RAG queries, or retrieved private context by default.
- Keep prompt secret detection enabled for coding-agent and tenant workflows.
- Use reviewed egress catalog entries for agent network access.
- Install Python dependencies from hashed lockfiles in local tests and runtime images.
- Promote only images that pass high/critical vulnerability scans, have SBOM evidence, and are signed by digest.

The public threat model is maintained in [docs/threat-model.md](docs/threat-model.md).

## 1.0.0-rc.1 review notes (2026-10-09)

Reviewed gateway authentication and team/project authorization, browser CSRF/session
handling, OIDC groups, managed keys, approval gates, settings/spend, trigger ingress and
history, run cursors and CSV exports. This is a source review with local regression
tests, not a penetration test or a production deployment certification.

- Fixed browser authentication routes bypassing the configured rate limiter. With
  `RATE_LIMIT_ENABLED=true` and a nonzero request ceiling, sign-in, callback, session
  and logout requests share a peer-address bucket, independent of team and forwarded
  headers. This boundary fails closed on store failure even when inference is configured
  to fail open. Redis makes limits shared across replicas; memory limits are per process.
- Fixed Uvicorn access logs retaining OIDC callback query parameters. The auth logger
  filter removes the entire query before formatting. Configure ingress/proxy and any
  custom access loggers likewise; the gateway cannot redact logs made outside it.
- Moved the request-body ceiling outside authentication middleware. Signed webhook
  authentication previously read bodies before the limit ran. Both declared-length
  and chunked oversized bodies now return 413 before authentication touches them.
- Existing regressions cover double-submit CSRF on cookie writes, session rotation,
  expiry and revocation, OIDC issuer/audience/nonce/PKCE and group-policy invalidation,
  cross-team/project denials, quorum identity/idempotency, settings revisions, signed
  webhook replay protection, cursor validation and CSV formula protection. Provider
  transport errors and notification failures avoid logging credential-bearing URLs.

Production boundaries still require operator configuration: enable shared rate limits,
bound unauthenticated API abuse at ingress, use TLS/Secure cookies, restrict trusted
proxy addresses, and keep Temporal and workers inaccessible to tenants. Rate limiting
is off by default in the standalone chart; do not interpret an available control as an
enabled one. Captured content is policy-controlled and may still be sensitive; exports
outlive server retention. Live IdP, proxy, cluster isolation and store failover checks
remain in the [caller verification list](docs/release-verification.md#candidate-readiness-pass).

## Validation

Before security-sensitive handoff or release review, run:

```bash
make validate-full
make repo-security-scan
make dependency-lock-check
make image-scan
make release-gate-strict
```

If strict release gates fail because current evidence is missing or stale, regenerate the evidence instead of lowering thresholds.
