"""Batched embedding and bounded Qdrant upserts for ingestion and the knowledge bootstrap.

One embedding request per chunk, each on a fresh connection, dominated ingestion time, and
the whole corpus went to Qdrant in one upsert, which Qdrant rejects past its 32 MB request
limit (roughly 7,000 chunks of 384 dimensions).
"""

from __future__ import annotations

import asyncio
import json
import random

import httpx
import pytest
from app.embeddings import EMBEDDING_BATCH_SIZE, OpenAICompatibleEmbeddingProvider
from app.ingest import ChunkRecord, SourceRecord, upsert_chunks
from app.retriever import UPSERT_BATCH_SIZE, QdrantRetriever


class _EmbeddingServer:
    """Answers /v1/embeddings with [len(text), position] vectors, optionally out of order."""

    def __init__(self, shuffle=False, drop_one=False):
        self.batches = []
        self.shuffle = shuffle
        self.drop_one = drop_one

    def __call__(self, request):
        body = json.loads(request.content)
        inputs = body["input"]
        self.batches.append(len(inputs))
        data = [{"index": index, "embedding": [float(len(text)), float(index)]} for index, text in enumerate(inputs)]
        if self.shuffle:
            random.Random(7).shuffle(data)
        if self.drop_one:
            data = data[1:]
        return httpx.Response(200, json={"data": data})


def _provider():
    return OpenAICompatibleEmbeddingProvider("http://embeddings.local", "m", dimensions=2, timeout_seconds=1)


def _patch_sync_client(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(
        "app.embeddings.httpx.Client", lambda *a, **k: real_client(transport=httpx.MockTransport(handler))
    )


def test_embed_many_sends_bounded_batches_and_keeps_input_order(monkeypatch):
    server = _EmbeddingServer(shuffle=True)
    _patch_sync_client(monkeypatch, server)
    texts = ["x" * length for length in range(1, 76)]

    vectors = _provider().embed_many(texts)

    assert server.batches == [EMBEDDING_BATCH_SIZE, EMBEDDING_BATCH_SIZE, 75 - 2 * EMBEDDING_BATCH_SIZE]
    # The server answered each batch shuffled; vectors still line up with their inputs.
    assert [vector[0] for vector in vectors] == [float(length) for length in range(1, 76)]


def test_embed_many_rejects_a_response_with_missing_items(monkeypatch):
    _patch_sync_client(monkeypatch, _EmbeddingServer(drop_one=True))

    with pytest.raises(ValueError, match="must contain 3 data items"):
        _provider().embed_many(["a", "b", "c"])


def test_embed_many_async_batches_too():
    server = _EmbeddingServer(shuffle=True)
    provider = _provider()
    provider._async_client = httpx.AsyncClient(transport=httpx.MockTransport(server))

    vectors = asyncio.run(provider.embed_many_async(["a", "bb", "ccc"]))

    assert server.batches == [3]
    assert vectors == [[1.0, 0.0], [2.0, 1.0], [3.0, 2.0]]


def _chunks(tmp_path, count):
    source = SourceRecord("handbook", str(tmp_path), "internal", "standard", "team-a", "m", tmp_path)
    return [ChunkRecord(source, tmp_path / "guide.md", index, f"chunk {index}", "v1") for index in range(count)]


def test_ingest_upserts_in_bounded_batches(tmp_path, monkeypatch):
    server = _EmbeddingServer()
    upserts = []

    def handler(request):
        if request.url.host == "embeddings.local":
            return server(request)
        if request.method == "GET":
            return httpx.Response(404)
        if request.url.path.endswith("/points"):
            upserts.append(len(json.loads(request.content)["points"]))
        return httpx.Response(200, json={"result": {"status": "completed"}})

    # app.ingest and app.embeddings share the httpx module, so one handler serves both hosts.
    _patch_sync_client(monkeypatch, handler)
    count = 2 * UPSERT_BATCH_SIZE + 10

    summary = upsert_chunks(_chunks(tmp_path, count), _provider(), "http://qdrant:6333", "kb", 5.0)

    assert summary["upserted_chunks"] == count
    assert upserts == [UPSERT_BATCH_SIZE, UPSERT_BATCH_SIZE, 10]
    assert sum(server.batches) == count
    assert max(server.batches) == EMBEDDING_BATCH_SIZE


def test_bootstrap_embeds_in_batches_and_upserts_in_bounded_requests(tmp_path, monkeypatch):
    for index in range(UPSERT_BATCH_SIZE + 5):
        (tmp_path / f"doc{index:04d}.md").write_text(f"# Doc {index}\ncontent", encoding="utf-8")
    server = _EmbeddingServer()
    upserts = []

    def qdrant(request):
        if request.method == "PUT" and request.url.path.endswith("/points"):
            upserts.append(len(json.loads(request.content)["points"]))
        return httpx.Response(200, json={"result": {}})

    provider = _provider()
    provider._async_client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    retriever = QdrantRetriever.from_directory(tmp_path, "http://qdrant:6333", "kb", "v1", 1.0, 2, True, provider)
    monkeypatch.setattr(retriever, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(qdrant)))

    asyncio.run(retriever.bootstrap())

    assert upserts == [UPSERT_BATCH_SIZE, 5]
    assert len(server.batches) == -(-(UPSERT_BATCH_SIZE + 5) // EMBEDDING_BATCH_SIZE)
    assert retriever.status()["last_sync_status"] == "synced"
