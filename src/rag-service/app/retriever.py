"""Knowledge retrievers providing lexical and Qdrant-backed grounded context."""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import logging
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.embeddings import (
    TOKEN_PATTERN,
    EmbeddingProvider,
    HashEmbeddingProvider,
    content_terms,
    stem,
    tokenize,
)
from app.reranker import NoopReranker, RerankerProvider

LOGGER = logging.getLogger("agentworkflows.rag")
# How long a failed knowledge bootstrap waits before the next attempt.
BOOTSTRAP_RETRY_SECONDS = 5.0
# The same text formats the ingestion CLI reads (app.ingest.TEXT_SUFFIXES).
DOCUMENT_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
# Points per Qdrant upsert request. A 384-dimension point with a 1,200-character chunk is
# about 4 KB of JSON, so a batch stays near 1 MB, far below Qdrant's 32 MB request limit.
UPSERT_BATCH_SIZE = 256


@dataclass(frozen=True)
class KnowledgeDocument:
    """An indexed knowledge document with its text and token frequency counts.

    ``owner`` is the tenant that owns the document (empty when the corpus is unscoped).
    The lexical retriever matches it against the caller's tenant when tenant isolation is
    enabled, mirroring the Qdrant path's ``owner`` payload filter.
    """

    id: str
    title: str
    source: str
    content: str
    tokens: Counter[str]
    owner: str = ""


@dataclass(frozen=True)
class RetrievalResult:
    """A retrieved document with its relevance score and a context excerpt."""

    document: KnowledgeDocument
    score: float
    excerpt: str


class VectorStoreError(RuntimeError):
    """Raised when the vector store cannot be reached or bootstrapped."""


def _query_points(payload: Any) -> list[dict[str, Any]]:
    """Return the point objects of a Qdrant query response.

    Accepts both the ``/points/query`` shape (``result.points``) and the older search
    shape (``result`` is the list). Anything else is a vector-store fault, reported as
    VectorStoreError (503) rather than surfacing as an AttributeError (500).
    """
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        result = result.get("points")
    if not isinstance(result, list):
        raise VectorStoreError("qdrant query returned an unexpected response shape")
    return [point for point in result if isinstance(point, dict)]


def _finite_score(value: Any) -> float:
    """Return a point's similarity score, treating a missing or non-numeric one as zero."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    try:
        score = float(value)
    except OverflowError:
        return 0.0
    return score if math.isfinite(score) else 0.0


def _describe_error(exc: Exception) -> str:
    """Summarize a failure for the unauthenticated ``/healthz`` body.

    httpx messages embed the request URL, which names internal hosts; the status line or
    exception type is what an operator needs there, and the full error goes to the log.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{type(exc).__name__}: HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return type(exc).__name__
    return f"{type(exc).__name__}: {exc}"


def title_from_content(path: Path, content: str) -> str:
    """Return a document title: its first heading or first line, else the file name."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
        if stripped:
            return stripped[:120]
    return path.stem


def document_id_for(relative_path: str) -> str:
    """Return the document id of a corpus-relative path: no extension, ``/`` as ``-``."""
    return PurePosixPath(relative_path).with_suffix("").as_posix().replace("/", "-")


def unique_document_id(base_id: str, seen: set[str]) -> str:
    """Return ``base_id``, or it with the first free numeric suffix, and record it in ``seen``.

    Paths can collide on an id (``a/b.md`` and ``a-b.md``, ``x.md`` and ``x.txt``); suffixes
    are handed out in path order so every id names exactly one file.
    """
    doc_id = base_id
    suffix = 2
    while doc_id in seen:
        doc_id = f"{base_id}-{suffix}"
        suffix += 1
    seen.add(doc_id)
    return doc_id


def _read_document(path: Path) -> str | None:
    """Read a knowledge file as UTF-8, dropping a leading byte-order mark.

    The corpus is read at startup, so one bad file used to stop the service from starting.
    A file that is not valid UTF-8 is now decoded with replacement characters, and one
    that cannot be read is skipped; both are logged with the file name.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        LOGGER.warning("skipping knowledge file %s: %s", path, exc)
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        LOGGER.warning(
            "knowledge file %s is not valid UTF-8 (first bad byte at offset %d); undecodable bytes were replaced",
            path,
            exc.start,
        )
        return raw.decode("utf-8-sig", errors="replace")


def load_documents(document_dir: Path, owner: str = "") -> list[KnowledgeDocument]:
    """Load markdown and text files under the directory into knowledge documents.

    ``owner`` stamps every loaded document with the corpus tenant so the lexical retriever
    can enforce the same per-tenant scoping as the Qdrant path. The local single-tenant lab
    passes its default sandbox id; an unscoped corpus leaves it empty.

    Document ids come from :func:`document_id_for`, made unique by :func:`unique_document_id`.
    """
    if not document_dir.exists():
        return []
    documents: list[KnowledgeDocument] = []
    seen_ids: set[str] = set()
    for path in sorted(document_dir.rglob("*")):
        relative_path = path.relative_to(document_dir)
        if any(part.startswith(".") for part in relative_path.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in DOCUMENT_SUFFIXES:
            continue
        content = _read_document(path)
        if content is None:
            continue
        relative = relative_path.as_posix()
        base_id = document_id_for(relative)
        doc_id = unique_document_id(base_id, seen_ids)
        if doc_id != base_id:
            LOGGER.warning("knowledge file %s shares the document id %r; serving it as %r", relative, base_id, doc_id)
        documents.append(
            KnowledgeDocument(
                id=doc_id,
                title=title_from_content(path, content),
                source=relative,
                content=content,
                tokens=Counter(tokenize(content)),
                owner=owner,
            )
        )
    return documents


def _compact(content: str) -> tuple[str, str]:
    """Return the content with whitespace runs collapsed, and its lowercase twin.

    Positions found in the lowercase text index the compact text, so the two must have
    the same length. ``str.lower`` keeps it for nearly every character; the few that grow
    (``İ`` lowers to two code points) are left as they are.
    """
    compact = " ".join(content.split())
    lowered = compact.lower()
    if len(lowered) != len(compact):
        lowered = "".join(char if len(char.lower()) != 1 else char.lower() for char in compact)
    return compact, lowered


def _window(compact: str, match: int, max_chars: int) -> str:
    """Return up to ``max_chars`` of text starting shortly before position ``match``.

    The lead-in is at most a quarter of the window: a fixed 160 characters used to push
    the match itself out of any window shorter than that.
    """
    start = max(match - min(160, max_chars // 4), 0)
    end = min(start + max_chars, len(compact))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end]}{suffix}"


def _excerpt(content: str, terms: Iterable[str], max_chars: int = 700) -> str:
    """Return an excerpt anchored on the earliest occurrence of any of the terms."""
    compact, lowered = _compact(content)
    positions = [position for position in (lowered.find(term) for term in terms) if position >= 0]
    return _window(compact, min(positions, default=0), max_chars)


# BM25 parameters: the usual Lucene/Elasticsearch defaults.
BM25_K1 = 1.2
BM25_B = 0.75
# A query term in a document's title counts as this many extra IDF-weighted matches.
TITLE_WEIGHT = 1.0
# Bonus, in IDF-weighted matches per query term, for a document containing the query verbatim.
PHRASE_WEIGHT = 0.5


def _idf(document_count: int, document_frequency: int) -> float:
    """Return the BM25 inverse document frequency (the always-positive Lucene variant)."""
    return math.log(1.0 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5))


class _LexicalIndex:
    """An inverted BM25 index over one tenant's documents.

    Built once, when the retriever is created: a query then touches only the postings of
    its own terms instead of rescanning and lowercasing every document, and ranking uses
    term rarity and document length, so long documents full of common words no longer win
    every query. Terms are indexed by their plural-folded stem; a document's title field
    is its title plus its file name, which in a curated corpus usually names the topic.
    """

    def __init__(self, documents: list[KnowledgeDocument]) -> None:
        self.documents = documents
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.title_postings: dict[str, list[int]] = {}
        lengths: list[int] = []
        stem_of: dict[str, str] = {}
        for index, document in enumerate(documents):
            lengths.append(sum(document.tokens.values()))
            stems: dict[str, int] = {}
            for term, count in document.tokens.items():
                key = stem_of.get(term)
                if key is None:
                    key = stem_of[term] = stem(term)
                stems[key] = stems.get(key, 0) + count
            for key, count in stems.items():
                self.postings.setdefault(key, []).append((index, count))
            title_field = f"{document.title} {Path(document.source).stem}"
            for key in {stem(term) for term in tokenize(title_field)}:
                self.title_postings.setdefault(key, []).append(index)
        average = sum(lengths) / len(lengths) if lengths else 0.0
        self.length_norms = [
            BM25_K1 * (1.0 - BM25_B + BM25_B * length / average) if average else BM25_K1 for length in lengths
        ]
        count = len(documents)
        self.idf = {term: _idf(count, len(postings)) for term, postings in self.postings.items()}
        for term, titles in self.title_postings.items():
            self.idf.setdefault(term, _idf(count, len(titles)))
        self.texts = [_compact(document.content) for document in documents]

    def score(self, query: str, terms: list[str]) -> dict[int, float]:
        """Return BM25 (plus title and phrase bonus) scores of the documents matching any term.

        Stopwords are left out of the query unless it has nothing else.
        """
        scores: dict[int, float] = {}
        unique_terms = list(dict.fromkeys(stem(term) for term in content_terms(terms)))
        for term in unique_terms:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for index, count in self.postings.get(term, ()):
                saturation = count * (BM25_K1 + 1.0) / (count + self.length_norms[index])
                scores[index] = scores.get(index, 0.0) + idf * saturation
            for index in self.title_postings.get(term, ()):
                scores[index] = scores.get(index, 0.0) + TITLE_WEIGHT * idf
        words = TOKEN_PATTERN.findall(query.lower())
        if len(words) > 1:
            self._add_phrase_bonus(" ".join(words), unique_terms, scores)
        return scores

    def _add_phrase_bonus(self, phrase: str, terms: list[str], scores: dict[int, float]) -> None:
        """Reward documents that contain the multi-word query verbatim.

        Only documents holding every query term can contain the phrase, so the substring
        search runs over that intersection, rarest term first, not the whole corpus.
        """
        postings = sorted((self.postings.get(term, []) for term in terms), key=len)
        if not postings or not postings[0]:
            return
        candidates = {index for index, _ in postings[0]}
        for posting in postings[1:]:
            candidates &= {index for index, _ in posting}
            if not candidates:
                return
        bonus = PHRASE_WEIGHT * sum(self.idf[term] for term in terms)
        for index in candidates:
            if phrase in self.texts[index][1]:
                scores[index] += bonus

    def excerpt(self, index: int, anchors: list[str], max_chars: int) -> str:
        """Return an excerpt of a document anchored on the first of ``anchors`` it contains.

        Anchors come in order of preference (rarest query term first), so the window lands
        on the most telling term rather than on wherever "the" first appears.
        """
        compact, lowered = self.texts[index]
        for term in anchors:
            position = lowered.find(term)
            if position >= 0:
                return _window(compact, position, max_chars)
        return _window(compact, 0, max_chars)


class LexicalRetriever:
    """In-memory BM25 retriever over the local knowledge corpus."""

    def __init__(
        self,
        documents: list[KnowledgeDocument],
        tenant_isolation_enabled: bool = False,
        tenant_field: str = "owner",
    ) -> None:
        self.documents = documents
        self.tenant_isolation_enabled = tenant_isolation_enabled
        # Accepted for parity with the Qdrant retriever; the lexical corpus only carries the
        # ``owner`` attribute, so a non-``owner`` field simply matches no document (fail closed).
        self.tenant_field = tenant_field
        # Under isolation every tenant gets its own index, so corpus statistics (term
        # rarity, average length) and hence the scores a caller sees never depend on what
        # another tenant's documents contain.
        self._indexes: dict[str | None, _LexicalIndex] = {}
        if not tenant_isolation_enabled:
            self._indexes[None] = _LexicalIndex(documents)
        elif tenant_field == "owner":
            by_owner: dict[str, list[KnowledgeDocument]] = {}
            for document in documents:
                by_owner.setdefault(document.owner, []).append(document)
            self._indexes.update((owner, _LexicalIndex(owned)) for owner, owned in by_owner.items())

    @classmethod
    def from_directory(
        cls,
        document_dir: Path,
        owner: str = "",
        tenant_isolation_enabled: bool = False,
        tenant_field: str = "owner",
    ) -> LexicalRetriever:
        """Build a lexical retriever from documents loaded under a directory.

        ``owner`` stamps the loaded corpus so per-tenant isolation can be enforced; the local
        lab passes its default sandbox id.
        """
        return cls(
            load_documents(document_dir, owner=owner),
            tenant_isolation_enabled=tenant_isolation_enabled,
            tenant_field=tenant_field,
        )

    def _index_for(self, tenant: str | None) -> _LexicalIndex | None:
        """Return the index a caller may search, or None when isolation leaves it nothing."""
        if not self.tenant_isolation_enabled:
            return self._indexes[None]
        if not tenant:
            return None
        return self._indexes.get(tenant)

    async def query(
        self, query: str, top_k: int, max_context_chars: int, tenant: str | None = None
    ) -> list[RetrievalResult]:
        """Return the top-k documents ranked by BM25 with title and phrase bonuses.

        When ``tenant_isolation_enabled``, only documents whose owner equals ``tenant`` are
        eligible, mirroring the Qdrant path's ``owner`` payload filter. Isolation fails closed:
        a query with no resolvable ``tenant`` (or a corpus with no matching owner) returns
        nothing rather than the whole corpus. With isolation off the lexical backend serves its
        single local corpus to every caller (local-lab default).
        """
        index = self._index_for(tenant)
        if index is None:
            return []
        terms = tokenize(query)
        if not terms:
            return []
        scores = index.score(query, terms)
        top = heapq.nsmallest(top_k, scores.items(), key=lambda item: (-item[1], index.documents[item[0]].id))
        # Anchor each excerpt on the rarest query term the document holds.
        anchors = sorted(set(content_terms(terms)), key=lambda term: -index.idf.get(stem(term), 0.0))
        return [
            RetrievalResult(
                document=index.documents[position],
                score=score,
                excerpt=index.excerpt(position, anchors, max_context_chars),
            )
            for position, score in top
        ]


class QdrantRetriever:
    """Vector retriever that embeds queries and searches a Qdrant collection."""

    def __init__(
        self,
        documents: list[KnowledgeDocument],
        base_url: str,
        collection: str,
        collection_version: str,
        timeout_seconds: float,
        vector_dimensions: int,
        bootstrap_from_knowledge: bool,
        embedding_provider: EmbeddingProvider | None = None,
        candidate_multiplier: int = 4,
        lexical_weight: float = 0.5,
        allowed_classifications: tuple[str, ...] = (),
        reranker_provider: RerankerProvider | None = None,
        tenant_isolation_enabled: bool = False,
        tenant_field: str = "owner",
    ) -> None:
        self.documents = documents
        self.base_url = base_url.rstrip("/")
        self.collection = collection
        self.collection_version = collection_version
        self.timeout_seconds = timeout_seconds
        self.vector_dimensions = vector_dimensions
        self.bootstrap_from_knowledge = bootstrap_from_knowledge
        self.embedding_provider = embedding_provider or HashEmbeddingProvider(vector_dimensions)
        self.candidate_multiplier = candidate_multiplier
        self.lexical_weight = lexical_weight
        self.allowed_classifications = allowed_classifications
        self.reranker_provider: RerankerProvider = reranker_provider or NoopReranker()
        self.tenant_isolation_enabled = tenant_isolation_enabled
        self.tenant_field = tenant_field
        self._bootstrapped = False
        self._bootstrap_lock = asyncio.Lock()
        self._bootstrap_retry_at = 0.0
        self._async_client: httpx.AsyncClient | None = None
        self.last_sync_status = "pending" if bootstrap_from_knowledge else "disabled"
        self.last_sync_error = ""

    @classmethod
    def from_directory(
        cls,
        document_dir: Path,
        base_url: str,
        collection: str,
        collection_version: str,
        timeout_seconds: float,
        vector_dimensions: int,
        bootstrap_from_knowledge: bool,
        embedding_provider: EmbeddingProvider | None = None,
        candidate_multiplier: int = 4,
        lexical_weight: float = 0.5,
        allowed_classifications: tuple[str, ...] = (),
        reranker_provider: RerankerProvider | None = None,
        tenant_isolation_enabled: bool = False,
        tenant_field: str = "owner",
    ) -> QdrantRetriever:
        """Build a Qdrant retriever seeded with documents loaded from a directory."""
        return cls(
            documents=load_documents(document_dir),
            base_url=base_url,
            collection=collection,
            collection_version=collection_version,
            timeout_seconds=timeout_seconds,
            vector_dimensions=vector_dimensions,
            bootstrap_from_knowledge=bootstrap_from_knowledge,
            embedding_provider=embedding_provider,
            candidate_multiplier=candidate_multiplier,
            lexical_weight=lexical_weight,
            allowed_classifications=allowed_classifications,
            reranker_provider=reranker_provider,
            tenant_isolation_enabled=tenant_isolation_enabled,
            tenant_field=tenant_field,
        )

    def status(self) -> dict[str, str | int | bool]:
        """Return collection, embedding, and last-sync status for health checks."""
        return {
            "collection": self.collection,
            "collection_version": self.collection_version,
            "documents": len(self.documents),
            "vector_dimensions": self.vector_dimensions,
            "embedding_provider": self.embedding_provider.name,
            "embedding_model": self.embedding_provider.model,
            "bootstrap_from_knowledge": self.bootstrap_from_knowledge,
            "last_sync_status": self.last_sync_status,
            "last_sync_error": self.last_sync_error,
        }

    def _client(self) -> httpx.AsyncClient:
        """Return the shared async client, creating it on first use.

        Reusing one client keeps the connection pool to Qdrant warm instead of paying
        a new TCP handshake on every bootstrap, readiness, and query call.
        """
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._async_client

    async def aclose(self) -> None:
        """Close the shared async client; called on service shutdown."""
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    async def _ensure_collection(self, client: httpx.AsyncClient) -> None:
        collection_url = f"{self.base_url}/collections/{self.collection}"
        response = await client.get(collection_url)
        if response.status_code == 404:
            response = await client.put(
                collection_url,
                json={
                    "vectors": {
                        "size": self.vector_dimensions,
                        "distance": "Cosine",
                    }
                },
            )
        response.raise_for_status()

    async def _upsert_documents(self, client: httpx.AsyncClient) -> None:
        if not self.documents:
            self.last_sync_status = "empty"
            return
        now = datetime.now(UTC)
        ingested_at = now.isoformat()
        ingested_at_epoch = int(now.timestamp())
        # One batched embedding call per EMBEDDING_BATCH_SIZE documents, not one per document.
        vectors = await self.embedding_provider.embed_many_async([document.content for document in self.documents])
        points = []
        for document, vector in zip(self.documents, vectors, strict=True):
            points.append(
                {
                    "id": str(uuid5(NAMESPACE_URL, f"{self.collection_version}:{document.source}:{document.id}")),
                    "vector": vector,
                    "payload": {
                        "collection_version": self.collection_version,
                        "document_id": document.id,
                        "title": document.title,
                        "source": document.source,
                        "content": document.content,
                        "ingestedAt": ingested_at,
                        "ingestedAtEpoch": ingested_at_epoch,
                        # Same access-control fields the ingestion job stamps (values
                        # mirror platform/rag/sources/platform-knowledge.yaml) so the
                        # classification allowlist does not silently filter out every
                        # bootstrapped document.
                        "classification": "internal",
                        "owner": "platform-team",
                    },
                }
            )
        # Bounded requests: Qdrant rejects bodies over its max_request_size_mb (32 MB default).
        for start in range(0, len(points), UPSERT_BATCH_SIZE):
            response = await client.put(
                f"{self.base_url}/collections/{self.collection}/points",
                params={"wait": "true"},
                json={"points": points[start : start + UPSERT_BATCH_SIZE]},
            )
            response.raise_for_status()
        self.last_sync_status = "synced"
        self.last_sync_error = ""

    async def _ensure_bootstrapped(self) -> None:
        """Sync the local knowledge corpus into the collection once, raising VectorStoreError on failure.

        One attempt runs at a time, and a failed attempt is not retried for
        ``BOOTSTRAP_RETRY_SECONDS``: during an outage a burst of queries otherwise queued
        a full bootstrap each (every document re-embedded and re-uploaded) behind one another.
        """
        if self._bootstrapped or not self.bootstrap_from_knowledge:
            return
        async with self._bootstrap_lock:
            if self._bootstrapped:
                return
            if monotonic() < self._bootstrap_retry_at:
                raise VectorStoreError("qdrant bootstrap failed recently; waiting before the next attempt")
            try:
                client = self._client()
                await self._ensure_collection(client)
                await self._upsert_documents(client)
            except (httpx.HTTPError, ValueError, IndexError) as exc:
                # ValueError/IndexError: a malformed or wrong-dimension embedding response.
                self._bootstrap_retry_at = monotonic() + BOOTSTRAP_RETRY_SECONDS
                self.last_sync_status = "failed"
                self.last_sync_error = _describe_error(exc)
                LOGGER.warning("knowledge bootstrap into qdrant failed: %s", exc)
                raise VectorStoreError("qdrant bootstrap failed") from exc
            self._bootstrapped = True

    async def bootstrap(self) -> None:
        """Eagerly bootstrap the collection at startup so reachability surfaces early.

        Avoids a cold-start thundering herd where every concurrent first request
        races to create/upsert the collection. Failures propagate as VectorStoreError.
        """
        await self._ensure_bootstrapped()

    async def ping(self) -> bool:
        """Return whether the Qdrant collection endpoint is reachable for readiness."""
        try:
            response = await self._client().get(f"{self.base_url}/collections/{self.collection}")
            response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    def _query_filter(self, tenant: str | None = None) -> dict[str, Any]:
        """Build the Qdrant filter: collection version, classification allowlist, tenant scope.

        ``allowed_classifications`` (when set) access-scopes retrieval so a caller only
        sees documents whose ``classification`` payload field is in the allowlist; empty
        returns every classification.

        When ``tenant_isolation_enabled`` and a tenant is supplied, a ``tenant_field``
        (default ``owner``, stamped per point at ingest) match is appended so a caller only
        retrieves documents owned by its own tenant, closing cross-tenant retrieval in the
        shared collection. Fails closed: a tenant with no matching documents gets none, and a
        query with no resolvable tenant is rejected before this filter is built (see ``query``).
        """
        must: list[dict[str, Any]] = [{"key": "collection_version", "match": {"value": self.collection_version}}]
        if self.allowed_classifications:
            must.append({"key": "classification", "match": {"any": list(self.allowed_classifications)}})
        if self.tenant_isolation_enabled and tenant:
            must.append({"key": self.tenant_field, "match": {"value": tenant}})
        return {"must": must}

    def _hybrid_score(self, dense_score: float, query_terms: set[str], content: str) -> float:
        """Blend the dense cosine score with lexical query-term overlap.

        Lexical overlap (the fraction of query terms present in the document) complements
        the dense signal, which materially improves ranking under the default hashed-vector
        embedding. ``lexical_weight`` of 0 reproduces pure dense ranking.
        """
        dense = max(0.0, dense_score)
        if not query_terms:
            return dense
        # Substring match (not exact token-set intersection): the tokenizer keeps trailing
        # punctuation, so "gateway." would otherwise miss the query term "gateway".
        content_lower = content.lower()
        hits = sum(1 for term in query_terms if term in content_lower)
        overlap = hits / len(query_terms)
        weight = self.lexical_weight
        return (1.0 - weight) * dense + weight * overlap

    async def query(
        self, query: str, top_k: int, max_context_chars: int, tenant: str | None = None
    ) -> list[RetrievalResult]:
        """Embed the query, fetch candidates, and return the reranked top-k points.

        Candidates are over-fetched and reordered by the hybrid (dense + lexical) score; when a
        cross-encoder reranker is configured it is applied as a precision-oriented second stage.
        When tenant isolation is enabled, ``tenant`` scopes retrieval to that tenant's documents;
        a query with no resolvable ``tenant`` fails closed (returns nothing) rather than searching
        the whole collection unfiltered.
        """
        if self.tenant_isolation_enabled and not tenant:
            return []
        terms = set(tokenize(query))
        if not terms:
            return []
        # Bootstrap copies the local corpus into the collection; it is not a precondition
        # for searching it. A store that is really down still fails the search below (503),
        # while a store that only rejected the sync keeps serving what it holds.
        with contextlib.suppress(VectorStoreError):
            await self._ensure_bootstrapped()
        try:
            vector = await self.embedding_provider.embed_async(query)
        except (httpx.HTTPError, ValueError, IndexError) as exc:
            # The embedding endpoint is a retrieval dependency like Qdrant: an outage or a
            # malformed/wrong-dimension response is a retryable 503, not an unhandled 500.
            raise VectorStoreError("query embedding failed") from exc
        # Over-fetch dense candidates so the lexical rerank has room to reorder.
        candidate_limit = top_k * self.candidate_multiplier
        try:
            response = await self._client().post(
                f"{self.base_url}/collections/{self.collection}/points/query",
                json={
                    "query": vector,
                    "limit": candidate_limit,
                    "with_payload": True,
                    "filter": self._query_filter(tenant),
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # ValueError: a 200 whose body is not JSON (a proxy error page, a truncated body).
            raise VectorStoreError("qdrant query failed") from exc

        matches: list[RetrievalResult] = []
        for point in _query_points(payload):
            point_payload = point.get("payload") or {}
            if not isinstance(point_payload, dict):
                point_payload = {}
            content = str(point_payload.get("content") or point_payload.get("text") or "")
            # Chunks written by app.ingest carry the document's path under its source. Older
            # chunks have no document_id, and their "source" is the manifest location (often
            # an absolute path on the ingesting host), so derive both from the document path.
            document_path = point_payload.get("document_path")
            derived_id = document_id_for(str(document_path)) if document_path else None
            source = str(document_path or point_payload.get("source") or "qdrant")
            doc_id = str(point_payload.get("document_id") or derived_id or point_payload.get("id") or point.get("id"))
            title = str(point_payload.get("title") or doc_id)
            combined = self._hybrid_score(_finite_score(point.get("score")), terms, content)
            matches.append(
                RetrievalResult(
                    document=KnowledgeDocument(
                        id=doc_id,
                        title=title,
                        source=source,
                        content=content,
                        # Token counts are unread on this path (hybrid scoring substring-
                        # matches content), so skip tokenizing every over-fetched candidate.
                        tokens=Counter(),
                    ),
                    score=combined,
                    excerpt=_excerpt(content or title, terms, max_context_chars),
                )
            )
        matches = await self._maybe_rerank(query, matches)
        matches.sort(key=lambda match: (-match.score, match.document.id))
        return matches[:top_k]

    async def _maybe_rerank(self, query: str, matches: list[RetrievalResult]) -> list[RetrievalResult]:
        """Reorder candidates with the cross-encoder reranker when one is configured.

        Replaces each candidate's first-stage hybrid score with the reranker's relevance score.
        A reranker outage is non-fatal: on error the first-stage (hybrid) ranking is kept so a
        reranker dependency failure degrades quality rather than failing the query.
        """
        if self.reranker_provider.name == "none" or not matches:
            return matches
        try:
            scores = await self.reranker_provider.rerank_async(query, [match.document.content for match in matches])
        except (httpx.HTTPError, ValueError):
            # ValueError covers a non-JSON body and a response without a results list.
            return matches
        if len(scores) != len(matches):
            return matches
        return [
            RetrievalResult(document=match.document, score=score, excerpt=match.excerpt)
            for match, score in zip(matches, scores, strict=False)
        ]


def build_context(results: list[RetrievalResult], max_context_chars: int) -> str:
    """Concatenate retrieval excerpts into a context block within the char budget.

    The blank-line separators between sections count against the budget too, so the
    returned block is never longer than `max_context_chars`.
    """
    separator = "\n\n"
    sections: list[str] = []
    used = 0
    for result in results:
        header = f"[{result.document.id}] {result.document.title} ({result.document.source})"
        section = f"{header}\n{result.excerpt}"
        remaining = max_context_chars - used - (len(separator) if sections else 0)
        if remaining <= 0:
            break
        if len(section) > remaining:
            section = section[:remaining].rstrip()
        if not section:
            break
        used += len(section) + (len(separator) if sections else 0)
        sections.append(section)
    return separator.join(sections)
