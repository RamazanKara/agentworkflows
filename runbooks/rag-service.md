# RAG Service Runbook

Use this runbook when coding agents or lab users need approved platform context before calling the inference gateway.

## What It Provides

The RAG service exposes:

- `GET /healthz`
- `GET /metrics`
- `GET /v1/rag/documents`
- `POST /v1/rag/query`

Queries return retrieved document excerpts, a query SHA-256 fingerprint, optional context, and OpenAI-compatible `grounded_messages` that can be passed to the inference gateway. Audit logs record the query length and hash in place of raw query text.

When `auth.enabled` is true, `POST /v1/rag/query` and `GET /v1/rag/documents` require `X-API-Key` or `Authorization: Bearer`. Health and metrics remain unauthenticated for Kubernetes probes and scraping.

When `auth.jwt.enabled` is true (see "Trust boundary" below), the service additionally verifies its own audience-bound bearer token (JWKS/issuer/audience/exp/nbf) and derives the caller's tenant from the verified claim. With both enabled, send the API key in `X-API-Key` and the JWT in `Authorization: Bearer`; the service reads a JWT-shaped bearer value as a JWT. `auth.jwt.audience` is required whenever JWT verification is on, so a token minted for another service (for example the gateway) is rejected.

The local profile uses lexical retrieval: a BM25 index over the knowledge directory, built at startup, that weighs rare terms above common ones, normalizes for document length, boosts matches in a document's title or file name, and ignores English question words such as "how do I". It reads words in any script (CJK text is matched by character pairs) and folds English plurals. Customer values can switch to the Qdrant vector-store profile with `retrieval.backend=qdrant`; see `runbooks/vector-rag.md` for storage, network, and collection operations.

## Local Validation

Deploy the local stack, then run:

    make rag-smoke

The smoke test port-forwards the RAG service, sends `X-Request-ID`, `X-Sandbox-ID`, and `traceparent`, then verifies retrieved results and grounded messages.

## Add Knowledge

Edit `deploy/charts/rag-service/values.yaml` or an environment-specific values file:

    knowledge:
      documents:
        coding-standards.md: |
          # Coding Standards
          ...

For customer clusters, prefer environment-specific values or a chart override sourced from the customer's internal Git repository.

The service reads `.md`, `.markdown` and `.txt` files at startup. A file that is not valid UTF-8 is loaded with replacement characters and a file that cannot be read is skipped; both are logged with the file name instead of stopping the service. A document's id is its path without the extension, with `/` turned into `-`; when two paths map to the same id (`a/b.md` and `a-b.md`), the later one gets a numeric suffix and a warning is logged.

## Source Metadata And Ingestion

Customer document ingestion starts with a `platform.ai/v1alpha1` `RagSourceManifest`:

    apiVersion: platform.ai/v1alpha1
    kind: RagSourceManifest
    spec:
      sources:
        - id: platform-docs
          source: docs
          classification: internal
          retentionClass: project-documentation
          owner: platform-team
          embeddingModel: hash-text-v1

Validate a source manifest locally without writing vectors. This runs the same ingestion CLI as the chart's ingestion Job (`make test-rag` creates the service virtualenv):

    PYTHONPATH=src/rag-service src/rag-service/.venv/bin/python -m app.ingest \
      --source platform/rag/sources/platform-knowledge.yaml \
      --backend qdrant \
      --collection-version v1 \
      --check

Write to Qdrant only after source ownership, classification, retention class, and embedding model have been approved:

    QDRANT_URL=http://qdrant-vector-store.vector.svc.cluster.local:6333 \
    PYTHONPATH=src/rag-service src/rag-service/.venv/bin/python -m app.ingest \
      --source platform/rag/sources/platform-knowledge.yaml \
      --backend qdrant \
      --collection-version v1 \
      --write

Options not given on the command line fall back to the service's environment variables (`QDRANT_URL`, `QDRANT_COLLECTION`, `QDRANT_VECTOR_DIMENSIONS`, `RAG_EMBEDDING_PROVIDER`, `RAG_EMBEDDING_BASE_URL`, `RAG_EMBEDDING_MODEL`); `--status-file` also writes the JSON summary to a file.

What `--write` does:

- Chunks each document at roughly `--chunk-chars`, ending chunks on a paragraph, line, sentence, or word break. Every chunk records its document's id (the knowledge-directory rule, unique across the manifest), title, and path, which is what query results show.
- Embeds 32 chunks per embedding request and writes 256 points per Qdrant upsert, so large corpora stay under Qdrant's request size limit. A batch request gets `--timeout-seconds` per input.
- After every batch has been written, deletes the chunks of the manifest's sources (in that collection version) that this run did not write: chunks of files removed from a source and the tail chunks of documents that got shorter. A failed run deletes nothing. Pass `--no-prune` to keep them. Removing a whole source from the manifest still needs `--delete --source-id <id>`.

A manifest field that is empty or not a plain value, a YAML syntax error, or a document that is not UTF-8 fails the run with `error: ...` naming the field or file. The CLI warns when a source's `owner` is not a sandbox id (lowercase letters, digits, hyphens), because tenant-isolated retrieval could never return those documents.

The Helm chart can run an optional ingestion Job when `sourceManifest.enabled=true` and `ingestion.enabled=true`.
The service stores `collection_version` in each Qdrant point and filters queries by `retrieval.vectorStore.collectionVersion`, which lets operators stage re-ingestion or embedding-model migrations in the same collection with old and new vectors kept apart.

## Per-Tenant Isolation

Per-tenant retrieval isolation is **enabled by default** (`RAG_RETRIEVAL_TENANT_ISOLATION_ENABLED` defaults `true`). When enabled, a query returns only documents whose tenant field (`RAG_RETRIEVAL_TENANT_FIELD`, default `owner`) equals the caller's `X-Sandbox-ID`.

- **Both backends enforce it.** The Qdrant path appends an `owner` match to the query filter; the lexical path stamps its on-disk corpus with the service's default sandbox id (`DEFAULT_SANDBOX_ID`) and applies the same owner scoping.
- **Fail-closed, always.** A query returns only documents that match the caller's tenant. A tenant assertion requires an *explicit* `X-Sandbox-ID`: a request that falls back to `DEFAULT_SANDBOX_ID` returns an empty result set. Every search is tenant-filtered.
- **Ingest stamps the owner.** Set each source's `owner` in the `RagSourceManifest` to the owning tenant/sandbox id (the ingestion CLI warns when it is not one). The bootstrap knowledge corpus is stamped `owner=platform-team`; with isolation on, those platform documents stay scoped to `platform-team` by design, in both query results and the document listing (the service logs this at startup).
- **Scores are per tenant.** The lexical backend keeps one index per owner under isolation, so the term statistics behind a caller's scores never include another tenant's documents.

The bundled **local lexical lab** ships with isolation **off** (`retrieval.tenantIsolation.enabled: false`) because it serves shared platform documents to every caller as a single tenant. **Set `retrieval.tenantIsolation.enabled: true` for multi-tenant / customer profiles** (the customer values file does; confirm it when handing off).

**Trust boundary.** By default the tenant id is the client-asserted `X-Sandbox-ID` header, set by a trusted upstream: the inference gateway derives `X-Sandbox-ID` from a verified JWT tenant claim (`JWT_TENANT_CLAIM`, rejecting mismatches), or a workspace egress proxy stamps it. Under header-trust the shared RAG API key authenticates the caller and the trusted upstream sets the tenant, so keep the RAG service reachable only via the gateway or a header-stamping proxy, and use the shared API key as an authentication control.

**Audience-bound JWT verification.** The RAG service verifies its **own** token so the tenant is derived from a *verified* claim. Set `auth.jwt.enabled: true` (with `jwksUrl`, `issuer`, `audience`, and `tenantClaim`; `RAG_JWT_*` env, mirroring the gateway's `jwt_auth`). When enabled, `POST /v1/rag/query` and `GET /v1/rag/documents`:

- Verify the bearer JWT against the JWKS (algorithm pinned to the `HS256`/`RS256`/`ES256` allowlist rather than read from the token header, which rejects alg-confusion; issuer/audience/exp/nbf enforced).
- **Derive the tenant from the verified `tenantClaim`** and use *that* for the tenant-isolation filter, validating `X-Sandbox-ID` against it: a header that contradicts the verified claim is rejected (`403 sandbox_identity_mismatch`), and a token that names no tenant is rejected (`403 sandbox_claim_invalid`).
- Fail closed when `auth.jwt.required: true`: a missing or invalid token is `401`. An unreachable JWKS issuer with no cached keys is `503` (retry), distinct from a token rejection; a transient outage is covered by the last-known-good key cache (`cacheSeconds`).

Header-trust applies when JWT is off (the default in the base chart and local lab). The customer overlay ships it on as an operator-completed template (`deploy/clusters/customer/values/rag-service.yaml`): replace the placeholder issuer/JWKS/audience with the real IdP, and point `tenantClaim` at the claim that uniquely identifies the tenant (matching the `owner` documents are ingested with). The overlay sets `required: true` for the multi-tenant profile, so every caller presents a verified token. Use `required: false` only where the RAG service is reachable exclusively through a component that stamps a verified `X-Sandbox-ID`.

## Troubleshooting

Check service health:

    kubectl -n rag get deploy,svc,pod
    kubectl -n rag logs deploy/rag-service-rag-service --tail=100

Check a query:

    kubectl -n rag port-forward svc/rag-service-rag-service 18083:8080
    curl -sS -H 'Content-Type: application/json' \
      -H 'X-Sandbox-ID: agent-lab' \
      -H "X-API-Key: ${PLATFORM_API_KEY:-local-development-only}" \
      -d '{"query":"coding agents gateway trace headers","top_k":2}' \
      http://127.0.0.1:18083/v1/rag/query | python3 -m json.tool

If results are empty, confirm the knowledge ConfigMap contains relevant `.md` or `.txt` documents and the deployment has rolled out.

If the backend is `qdrant`, also confirm the `vector` namespace service is reachable, the collection name, collection version, and vector dimensions match the RAG values, and the Qdrant PVC is bound.

A query that cannot reach Qdrant or the embedding endpoint, or gets a malformed answer from either, returns `503 vector_store_unavailable` with `Retry-After: 5`. `/healthz` reports the knowledge bootstrap under `vector_store.last_sync_status`, and `last_sync_error` gives the error type and HTTP status (the full error, with URLs, is in the service log). A failed bootstrap is retried by later queries at most every 5 seconds, and it does not block searching a collection that already exists.

A startup error from invalid configuration names the environment variables involved, for example `default_top_k (DEFAULT_TOP_K) must not exceed max_top_k (MAX_TOP_K)`. Endpoint URLs must include `http://` or `https://`, and timeouts must be finite positive numbers.
