# 0015. Shared service modules are copied per image and kept identical

- Status: Accepted
- Date: 2026-10-04
- Deciders: Platform maintainer

## Context

The inference gateway and the RAG service need the same request body limit, the same tracing
setup, and the same JWKS cache and JWT signature verification. Each service builds its image
from its own directory (`context: src/<service>` in `.github/workflows/ci.yml` and in both
Dockerfiles), so each image contains only code from that directory.

The modules are copied into each service, and copies need a guard to stay aligned: JWKS cache
fixes, constant-time API-key comparison, and audit persistence metrics must land in both services
together. A mechanical check makes that alignment automatic.

## Decision

Shared code stays copied into each service, and the copies must be byte-identical:

- `app/body_limit.py`, `app/tracing.py`, and `app/jwks.py` exist in both services with the same
  bytes. `make repo-hygiene` (part of `make validate`) fails when any pair differs, naming the
  file, so both copies merge together.
- A shared module takes its configuration as an explicit value, independent of a service's
  `Settings` class. `app/jwks.py` defines `JwtConfig`; each service's `app/jwt_auth.py` maps its own
  settings onto it and adds what only that service needs (the gateway's required scopes, the RAG
  service's tenant claim). Service-specific code lives only in service files.
- To share another module, move its service-independent part into a file listed in
  `SHARED_SERVICE_MODULES` in `scripts/repo-hygiene.py`, and keep the service-specific
  remainder in the service.

## Consequences

- A fix to shared code is one edit applied to two files, and CI requires both.
- Images stay self-contained: each build context holds everything its image needs, with a single
  build context per image.
- The audit chain helpers are separate implementations that share the hash primitives and the
  verifier (`scripts/audit-verify.py`). Their record shapes differ by design (`inference_request`
  and `rag_query`), and each service keeps its own.

## Alternatives considered

- **A shared Python package under `src/common/`, installed into both images.** A clean long-term
  layout that touches both Docker build contexts, the image CI jobs, the Dependabot directories,
  coverage, and the locks. Byte-identical copies were chosen as the right fit for three small
  modules.
- **A git submodule or vendored wheel.** Copies were chosen so code that changes together ships
  together, with no extra release step.
- **Manual review of copies.** The automated byte-identity check was chosen because it enforces
  alignment on every change.
