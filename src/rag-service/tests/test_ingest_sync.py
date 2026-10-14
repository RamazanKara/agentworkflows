"""Ingestion keeps Qdrant in step with the manifest and fails clearly on bad input.

Covers chunk boundaries, stale-chunk pruning after a re-ingest, the document identity that
retrieval results show for ingested chunks, manifest validation, and CLI error reporting.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from datetime import datetime, timedelta

import httpx
import pytest
from app import ingest
from app.embeddings import HashEmbeddingProvider
from app.ingest import build_chunks, chunk_text, load_manifest, owner_warnings
from app.retriever import QdrantRetriever

MANIFEST = """apiVersion: platform.ai/v1alpha1
kind: RagSourceManifest
spec:
  sources:
    - id: handbook
      source: docs
      classification: internal
      retentionClass: standard
      owner: {owner}
      embeddingModel: hash-text-v1
"""


def _manifest(tmp_path, owner="team-a"):
    path = tmp_path / "manifest.yaml"
    path.write_text(MANIFEST.format(owner=owner), encoding="utf-8")
    (tmp_path / "docs").mkdir(exist_ok=True)
    return path


# --- chunking -------------------------------------------------------------------------


def test_a_large_overlap_cannot_make_chunking_crawl():
    # Paragraph breaks just past mid-window and an overlap close to the chunk size used to
    # restart each chunk one character later: thousands of chunks for a 20 KB text.
    text = "\n\n".join("word " * 130 for _ in range(30))

    chunks = chunk_text(text, 1200, 1100)

    assert len(chunks) <= 4 * len(text) // 1200 + 1


def test_chunks_end_on_word_breaks_and_overlap_starts_on_a_word():
    words = [f"w{index:04d}" for index in range(600)]
    text = " ".join(words)

    chunks = chunk_text(text, 200, 40)

    vocabulary = set(words)
    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    # Every chunk consists of whole words only.
    assert all(set(chunk.split()) <= vocabulary for chunk in chunks)
    # Consecutive chunks share words (the overlap) without skipping any.
    for first, second in itertools.pairwise(chunks):
        assert second.split()[0] in first.split()


def test_sentence_breaks_are_preferred_over_word_breaks():
    text = ("This is a sentence about the gateway. " * 20).strip()

    chunks = chunk_text(text, 200, 0)

    assert all(chunk.endswith(".") for chunk in chunks)


# --- documents and manifests ----------------------------------------------------------


def test_chunks_name_their_document_and_ids_stay_unique_across_sources(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "guide.md").write_bytes("﻿# Guide A\nalpha".encode())
    (tmp_path / "b" / "guide.md").write_text("# Guide B\nbeta", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    second_source = """    - id: other
      source: b
      classification: internal
      retentionClass: standard
      owner: team-a
      embeddingModel: hash-text-v1
"""
    manifest.write_text(MANIFEST.format(owner="team-a").replace("source: docs", "source: a") + second_source)

    chunks = build_chunks(load_manifest(manifest), 200, 20)

    payloads = [chunk.payload() for chunk in chunks]
    assert [(p["source_id"], p["document_id"], p["title"]) for p in payloads] == [
        ("handbook", "guide", "Guide A"),
        ("other", "guide-2", "Guide B"),
    ]
    assert not payloads[0]["content"].startswith("﻿")


def test_a_document_that_is_not_utf8_fails_the_run_with_its_name(tmp_path):
    manifest = _manifest(tmp_path)
    (tmp_path / "docs" / "legacy.md").write_bytes("Gr\xf6\xdfe".encode("latin-1"))

    with pytest.raises(ValueError, match=r"legacy\.md is not valid UTF-8"):
        build_chunks(load_manifest(manifest), 200, 20)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("apiVersion: platform.ai/v1alpha1\nkind: RagSourceManifest\nspec: [1]\n", "spec must be a mapping"),
        ("apiVersion: platform.ai/v1alpha1\nkind: RagSourceManifest\nspec: {sources: [\n", "not valid YAML"),
        (MANIFEST.format(owner="null"), r"owner must be a non-empty string"),
        (MANIFEST.format(owner="[team-a]"), r"owner must be a non-empty string"),
        (MANIFEST.format(owner='""'), r"owner must be a non-empty string"),
    ],
)
def test_malformed_manifests_are_rejected_with_the_offending_field(tmp_path, body, message):
    path = tmp_path / "manifest.yaml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_manifest(path)


def test_owners_that_can_never_match_a_tenant_are_flagged(tmp_path):
    good = load_manifest(_manifest(tmp_path, owner="team-a"))
    bad = load_manifest(_manifest(tmp_path, owner="Platform Team"))

    assert owner_warnings(good) == []
    assert "never return its documents" in owner_warnings(bad)[0]


def test_cli_reports_operator_errors_without_a_traceback(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    (tmp_path / "docs" / "legacy.md").write_bytes(b"\xff\xfe")
    monkeypatch.setattr("sys.argv", ["ingest", "--source", str(manifest), "--check"])

    with pytest.raises(SystemExit) as raised:
        ingest.main()

    assert str(raised.value).startswith("error: source handbook:")


# --- syncing with Qdrant --------------------------------------------------------------


class _Store:
    """A minimal Qdrant: upserts by id and filtered deletes."""

    def __init__(self):
        self.points = {}
        self.deletes = []
        self.fail_upserts = False

    def _match(self, payload, condition):
        value = payload.get(condition["key"])
        if "range" in condition:
            return value is not None and value < condition["range"]["lt"]
        match = condition["match"]
        return value in match["any"] if "any" in match else value == match["value"]

    def __call__(self, request):
        if request.method == "GET":
            return httpx.Response(200, json={"result": {"config": {"params": {"vectors": {"size": 8}}}}})
        body = json.loads(request.content)
        if request.url.path.endswith("/points"):
            if self.fail_upserts:
                return httpx.Response(500)
            self.points.update({point["id"]: point["payload"] for point in body["points"]})
        elif request.url.path.endswith("/delete"):
            self.deletes.append(body["filter"])
            must = body["filter"]["must"]
            self.points = {
                key: payload
                for key, payload in self.points.items()
                if not all(self._match(payload, condition) for condition in must)
            }
        return httpx.Response(200, json={"result": {"status": "completed"}})


def _write(monkeypatch, manifest, store, *extra):
    real_client = httpx.Client
    monkeypatch.setattr(ingest.httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(store)))
    argv = ["ingest", "--source", str(manifest), "--write", "--qdrant-url", "http://qdrant:6333", "--dimensions", "8"]
    monkeypatch.setattr("sys.argv", [*argv, "--chunk-chars", "100", "--overlap-chars", "0", *extra])
    assert ingest.main() == 0


def test_reingesting_removes_chunks_of_shortened_and_deleted_documents(tmp_path, monkeypatch, capsys):
    manifest = _manifest(tmp_path)
    docs = tmp_path / "docs"
    (docs / "long.md").write_text("\n\n".join(f"Paragraph {n} " + "text " * 15 for n in range(4)), encoding="utf-8")
    (docs / "gone.md").write_text("This file will be deleted.", encoding="utf-8")
    store = _Store()
    _write(monkeypatch, manifest, store)
    assert {payload["document_id"] for payload in store.points.values()} == {"long", "gone"}
    assert len(store.points) == 5

    (docs / "long.md").write_text("Now a single short paragraph.", encoding="utf-8")
    (docs / "gone.md").unlink()
    capsys.readouterr()

    class _Later(datetime):
        # Chunk epochs have one-second resolution; the re-ingest happens a few seconds later.
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=5)

    monkeypatch.setattr(ingest, "datetime", _Later)
    _write(monkeypatch, manifest, store)

    assert [payload["content"] for payload in store.points.values()] == ["Now a single short paragraph."]
    summary = json.loads(capsys.readouterr().out)
    assert summary["pruned_sources"] == ["handbook"]


def test_no_prune_keeps_old_chunks_and_a_failed_write_prunes_nothing(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    (tmp_path / "docs" / "guide.md").write_text("Guide text.", encoding="utf-8")
    store = _Store()

    _write(monkeypatch, manifest, store, "--no-prune")
    assert store.deletes == []

    store.fail_upserts = True
    with pytest.raises(SystemExit, match="error: Server error '500"):
        _write(monkeypatch, manifest, store)
    assert store.deletes == []


def test_prune_filter_is_scoped_to_the_manifest_sources_and_version(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    (tmp_path / "docs" / "guide.md").write_text("Guide text.", encoding="utf-8")
    store = _Store()

    _write(monkeypatch, manifest, store, "--collection-version", "v7")

    [prune] = store.deletes
    assert {"key": "source_id", "match": {"any": ["handbook"]}} in prune["must"]
    assert {"key": "collection_version", "match": {"value": "v7"}} in prune["must"]
    assert any(condition["key"] == "ingestedAtEpoch" for condition in prune["must"])


def test_results_from_previously_ingested_chunks_name_their_document(tmp_path, monkeypatch):
    # Chunks ingested before document_id/title were stamped: the result used to carry the
    # point UUID as id and title, and the manifest source path (often absolute) as source.
    legacy = {
        "id": "5f0c6c0e-4d0b-5d36-9a43-0f1c5f7d3a10",
        "score": 0.9,
        "payload": {
            "source": "/srv/ingest/docs",
            "source_id": "handbook",
            "document_path": "runbooks/restore.md",
            "content": "Restore the gateway from backup.",
        },
    }
    retriever = QdrantRetriever([], "http://qdrant:6333", "kb", "v1", 1.0, 8, False, HashEmbeddingProvider(8))
    monkeypatch.setattr(
        retriever,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"result": {"points": [legacy]}}))
        ),
    )

    [result] = asyncio.run(retriever.query("restore gateway", 1, 200))

    assert result.document.id == "runbooks-restore"
    assert result.document.source == "runbooks/restore.md"
    assert result.document.title == "runbooks-restore"
