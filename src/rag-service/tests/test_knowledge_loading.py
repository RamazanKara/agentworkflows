"""Loading the on-disk knowledge corpus: encodings, unreadable files, and document ids.

The corpus is read when the app is created, so a single bad file used to keep the whole
service from starting. These tests pin the tolerant behaviour: decode what can be decoded,
skip what cannot be read, log both, and keep document ids unique.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.main import create_app
from app.retriever import load_documents
from app.settings import Settings
from fastapi.testclient import TestClient


def test_a_byte_order_mark_does_not_end_up_in_the_title_or_content(tmp_path):
    (tmp_path / "bom.md").write_bytes("﻿# Windows Title\nbody text".encode())

    [document] = load_documents(tmp_path)

    assert document.title == "Windows Title"
    assert not document.content.startswith("﻿")


def test_a_file_that_is_not_utf8_is_loaded_with_replacements_and_logged(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="agentworkflows.rag")
    (tmp_path / "latin1.md").write_bytes("# Gr\xf6\xdfe\nDie Datei ist Latin-1.".encode("latin-1"))
    (tmp_path / "ok.md").write_text("# Fine\nutf-8 text", encoding="utf-8")

    documents = {document.id: document for document in load_documents(tmp_path)}

    assert set(documents) == {"latin1", "ok"}
    assert "Datei ist Latin-1" in documents["latin1"].content
    assert "latin1.md is not valid UTF-8" in caplog.text


def test_an_unreadable_file_is_skipped_instead_of_failing_startup(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="agentworkflows.rag")
    (tmp_path / "locked.md").write_text("# Locked\nsecret", encoding="utf-8")
    (tmp_path / "open.md").write_text("# Open\ngateway", encoding="utf-8")
    real_read_bytes = Path.read_bytes

    def read_bytes(self):
        if self.name == "locked.md":
            raise PermissionError(13, "Permission denied")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    app = create_app(Settings(document_dir=tmp_path, retrieval_tenant_isolation_enabled=False))
    health = TestClient(app).get("/healthz").json()

    assert health["documents"] == 1
    assert "skipping knowledge file" in caplog.text


def test_colliding_paths_get_distinct_document_ids(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.md").write_text("# Nested\n", encoding="utf-8")
    (tmp_path / "a-b.md").write_text("# Flat\n", encoding="utf-8")
    (tmp_path / "x.md").write_text("# X markdown\n", encoding="utf-8")
    (tmp_path / "x.txt").write_text("X text\n", encoding="utf-8")

    documents = load_documents(tmp_path)

    ids = [document.id for document in documents]
    assert len(ids) == len(set(ids)) == 4
    assert {document.source: document.id for document in documents} == {
        "a/b.md": "a-b",
        "a-b.md": "a-b-2",
        "x.md": "x",
        "x.txt": "x-2",
    }


def test_markdown_extension_matches_the_ingestion_cli(tmp_path):
    (tmp_path / "guide.markdown").write_text("# Guide\ncontent", encoding="utf-8")

    [document] = load_documents(tmp_path)

    assert document.id == "guide"
