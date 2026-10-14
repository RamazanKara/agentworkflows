"""Vector-store and embedding faults must surface as retryable 503s, never as 500s.

Qdrant and the embedding endpoint are network dependencies: an outage, a proxy error page
served with a 200, or a malformed body are all "the store is unavailable" to a caller.
These tests also pin the knowledge bootstrap's behaviour under failure: one attempt at a
time, a short backoff after a failure, and a search that still runs against an existing
collection when only the sync was rejected.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from app.main import create_app
from app.retriever import QdrantRetriever, VectorStoreError
from app.settings import Settings
from fastapi.testclient import TestClient

QUERY_PATH = "/collections/lab/points/query"


def _retriever(tmp_path, bootstrap=False, embedding=None):
    (tmp_path / "agents.md").write_text("# Coding Agents\nUse the gateway.", encoding="utf-8")
    return QdrantRetriever.from_directory(
        tmp_path,
        "http://qdrant.internal.example:6333",
        "lab",
        "v1",
        timeout_seconds=1.0,
        vector_dimensions=16,
        bootstrap_from_knowledge=bootstrap,
        embedding_provider=embedding,
    )


def _use(retriever, monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(retriever, "_client", lambda: httpx.AsyncClient(transport=transport))


def _points(*points):
    return httpx.Response(200, json={"result": {"points": list(points)}})


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>bad gateway</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, json={"status": "ok"}),
        httpx.Response(200, json={"result": {"points": None}}),
    ],
    ids=["non-json", "list-body", "no-result", "null-points"],
)
def test_malformed_query_response_is_a_vector_store_error(tmp_path, monkeypatch, response):
    retriever = _retriever(tmp_path)
    _use(retriever, monkeypatch, lambda request: response)

    with pytest.raises(VectorStoreError):
        asyncio.run(retriever.query("gateway", top_k=1, max_context_chars=200))


def test_non_numeric_point_scores_are_treated_as_zero(tmp_path, monkeypatch):
    retriever = _retriever(tmp_path)
    _use(
        retriever,
        monkeypatch,
        lambda request: _points(
            {"id": "a", "score": "high", "payload": {"document_id": "a", "content": "gateway"}},
            {"id": "b", "score": None, "payload": {"document_id": "b", "content": "other"}},
            "not-a-point",
        ),
    )

    results = asyncio.run(retriever.query("gateway", top_k=2, max_context_chars=200))

    # Only the lexical half of the hybrid score remains: "a" contains the term, "b" does not.
    assert [result.document.id for result in results] == ["a", "b"]
    assert results[0].score == pytest.approx(0.5)
    assert results[1].score == 0.0


def test_malformed_vector_store_response_is_a_503_over_http(tmp_path, monkeypatch):
    (tmp_path / "agents.md").write_text("# Coding Agents\nUse the gateway.", encoding="utf-8")
    settings = Settings(
        document_dir=tmp_path,
        retrieval_backend="qdrant",
        vector_store_url="http://qdrant.local:6333",
        vector_collection="lab",
        vector_dimensions=16,
        vector_bootstrap_enabled=False,
        retrieval_tenant_isolation_enabled=False,
    )
    app = create_app(settings)
    _use(app.state.retriever, monkeypatch, lambda request: httpx.Response(200, text="not json"))

    response = TestClient(app).post("/v1/rag/query", json={"query": "gateway"})

    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "vector_store_unavailable"
    assert response.headers["Retry-After"] == "5"


def test_document_listing_matches_what_bootstrapped_retrieval_serves(tmp_path):
    (tmp_path / "agents.md").write_text("# Coding Agents\nUse the gateway.", encoding="utf-8")
    settings = Settings(
        document_dir=tmp_path,
        retrieval_backend="qdrant",
        vector_store_url="http://qdrant.local:6333",
        retrieval_tenant_isolation_enabled=True,
    )
    client = TestClient(create_app(settings))

    platform = client.get("/v1/rag/documents", headers={"X-Sandbox-ID": "platform-team"}).json()["documents"]
    other = client.get("/v1/rag/documents", headers={"X-Sandbox-ID": "team-a"}).json()["documents"]

    # Bootstrapped points are owned by platform-team; the listing used to show them to nobody.
    assert [document["id"] for document in platform] == ["agents"]
    assert other == []


class _WrongDimensionEmbedding:
    """An embedding endpoint that answers with the wrong vector size."""

    name = "openai-compatible"
    model = "m"
    dimensions = 16

    def __init__(self):
        self.calls = 0

    async def embed_async(self, value):
        self.calls += 1
        raise ValueError("embedding returned 3 dimensions; expected 16")

    async def embed_many_async(self, values):
        self.calls += 1
        raise ValueError("embedding returned 3 dimensions; expected 16")

    async def ready(self):
        return True


def test_bootstrap_embedding_error_does_not_crash_startup(tmp_path, monkeypatch):
    (tmp_path / "agents.md").write_text("# Coding Agents\nUse the gateway.", encoding="utf-8")
    settings = Settings(
        document_dir=tmp_path,
        retrieval_backend="qdrant",
        vector_store_url="http://qdrant.local:6333",
        vector_collection="lab",
        vector_dimensions=16,
        retrieval_tenant_isolation_enabled=False,
    )
    app = create_app(settings)
    app.state.retriever.embedding_provider = _WrongDimensionEmbedding()
    _use(app.state.retriever, monkeypatch, lambda request: httpx.Response(200, json={"result": {}}))

    # A ValueError used to escape the lifespan (which only caught VectorStoreError).
    with TestClient(app) as client:
        health = client.get("/healthz").json()

    assert health["vector_store"]["last_sync_status"] == "failed"
    assert "3 dimensions" in health["vector_store"]["last_sync_error"]


def test_failed_bootstrap_does_not_block_search_of_an_existing_collection(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "PUT" and request.url.path.endswith("/points"):
            return httpx.Response(400, json={"status": {"error": "payload too large"}})
        if request.url.path == QUERY_PATH:
            return _points({"id": "a", "score": 0.8, "payload": {"document_id": "agents", "content": "gateway"}})
        return httpx.Response(200, json={"result": {}})

    retriever = _retriever(tmp_path, bootstrap=True)
    _use(retriever, monkeypatch, handler)

    results = asyncio.run(retriever.query("gateway", top_k=1, max_context_chars=200))

    assert [result.document.id for result in results] == ["agents"]
    assert ("PUT", "/collections/lab/points") in calls
    assert retriever.status()["last_sync_status"] == "failed"


def test_concurrent_queries_during_an_outage_share_one_bootstrap_attempt(tmp_path, monkeypatch):
    attempts = []

    def handler(request):
        if request.url.path == "/collections/lab" and request.method == "GET":
            attempts.append(request.url.path)
        raise httpx.ConnectError("vector store down")

    retriever = _retriever(tmp_path, bootstrap=True)
    _use(retriever, monkeypatch, handler)

    async def burst():
        return await asyncio.gather(
            *(retriever.query("gateway", top_k=1, max_context_chars=200) for _ in range(10)),
            return_exceptions=True,
        )

    outcomes = asyncio.run(burst())

    assert all(isinstance(outcome, VectorStoreError) for outcome in outcomes)
    # One bootstrap attempt, then the backoff turns the other nine away immediately.
    assert len(attempts) == 1


def test_bootstrap_retries_after_the_backoff(tmp_path, monkeypatch):
    from app import retriever as retriever_module

    state = {"down": True}

    def handler(request):
        if state["down"]:
            raise httpx.ConnectError("vector store down")
        if request.url.path == QUERY_PATH:
            return _points()
        return httpx.Response(200, json={"result": {}})

    retriever = _retriever(tmp_path, bootstrap=True)
    _use(retriever, monkeypatch, handler)
    with pytest.raises(VectorStoreError):
        asyncio.run(retriever.bootstrap())

    state["down"] = False
    with pytest.raises(VectorStoreError, match="recently"):
        asyncio.run(retriever.bootstrap())
    monkeypatch.setattr(retriever_module, "monotonic", lambda: retriever._bootstrap_retry_at + 1)
    asyncio.run(retriever.bootstrap())

    assert retriever.status()["last_sync_status"] == "synced"


def test_health_reports_sync_errors_without_internal_urls(tmp_path, monkeypatch):
    retriever = _retriever(tmp_path, bootstrap=True)
    _use(retriever, monkeypatch, lambda request: httpx.Response(500, json={"status": "error"}))

    with pytest.raises(VectorStoreError):
        asyncio.run(retriever.bootstrap())

    error = retriever.status()["last_sync_error"]
    assert error == "HTTPStatusError: HTTP 500"
    assert "qdrant.internal.example" not in json.dumps(retriever.status())
