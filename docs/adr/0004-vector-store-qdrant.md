# 0004. Vector store: Qdrant

- Status: Accepted
- Date: 2026-07-01
- Deciders: Platform maintainer

## Context

The RAG service needs two retrieval modes from one codebase: a dependency-free lexical retriever that
runs in the laptop and CI lab, and a vector profile for customer knowledge bases that supports dense
similarity search with metadata filtering. The vector store has to run as a self-hosted workload
inside the same Kubernetes operating model (chart, policies, PVC encryption attestation, GitOps app),
stay provider-neutral, and expose a query API the RAG service can call directly.

## Decision

Use Qdrant as the optional self-hosted vector store, behind a retriever the RAG service can run with
or without it.

- The RAG service defines a `QdrantRetriever` alongside the default lexical retriever in
  [`src/rag-service/app/retriever.py`](https://github.com/RamazanKara/agentworkflows/blob/main/src/rag-service/app/retriever.py). The Qdrant path
  bootstraps a collection, embeds queries, over-fetches dense candidates, and applies a hybrid
  rerank that blends the dense cosine score with lexical query-term overlap (`lexical_weight`,
  default `0.5`; `0` reproduces pure dense ranking).
- Retrieval supports a metadata filter combining a collection version and a classification allowlist,
  so tenants can scope what a query may match.
- Qdrant ships as its own chart,
  [`deploy/charts/qdrant-vector-store`](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/charts/qdrant-vector-store) (`appVersion`
  `1.18.1`), described as an "optional local vector store profile," and as the `qdrant-vector-store`
  Argo CD application in the `vector` namespace
  ([`deploy/clusters/local/apps.yaml`](https://github.com/RamazanKara/agentworkflows/blob/main/deploy/clusters/local/apps.yaml)).
- The collection migration procedure is documented in
  [`runbooks/qdrant-migration.md`](https://github.com/RamazanKara/agentworkflows/blob/main/runbooks/qdrant-migration.md).

## Consequences

- The default lab runs on the lexical retriever, which keeps the quickstart light, and the
  Qdrant profile is opt-in for customers who need dense retrieval.
- Qdrant runs as a first-class platform workload: it is subject to the same Kyverno policies, carries
  the `platform.ai/encryption-at-rest` PVC attestation
  (see [0002](0002-policy-engine-kyverno.md)), and reconciles through Argo CD like everything else.
- The hybrid score is a deliberate design choice on top of Qdrant's search: lexical overlap
  materially improves ranking under the default hashed-vector embedding, which makes the profile
  useful from the first run, before a customer wires a production embedding model.
- The operator runs the vector database (storage, backup, version migration) and follows the
  migration runbook for collection schema/version changes.

## Alternatives considered

- **pgvector (Postgres extension).** Attractive when a team already operates Postgres and wants
  vectors next to relational data. Qdrant was preferred as the default because it gives the RAG
  service a purpose-built vector query API with native filtering as a single dedicated workload.
- **Milvus.** High-scale, feature-rich vector database. Qdrant was preferred as the default because
  one Qdrant chart is lighter to operate and keep current, which fits the "optional profile" goal.
- **Weaviate.** Capable vector database with a built-in module ecosystem. A reasonable alternative;
  Qdrant was chosen for a simple single-binary deployment that fits one chart and one PVC, and a
  query API the retriever maps onto cleanly.
- **A managed/SaaS vector store.** A self-hosted store was preferred because it keeps tenant
  knowledge-base data and the retrieval control point inside the customer-owned boundary, matching
  the provider-neutral premise of the self-hosted profiles.
