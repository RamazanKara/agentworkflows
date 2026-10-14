"""Settings validation: reject what can only fail later, and name the variable to change."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from app import settings as settings_module
from app.settings import ENV_BY_FIELD, Settings

DOCS = Path("/knowledge")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.0, -1.0])
@pytest.mark.parametrize("field", ["vector_timeout_seconds", "reranker_timeout_seconds", "jwt_request_timeout_seconds"])
def test_durations_must_be_finite_and_positive(field, value):
    with pytest.raises(ValueError, match=f"{field} must be a finite number greater than zero"):
        Settings(document_dir=DOCS, **{field: value})


def test_nan_from_the_environment_is_rejected_with_the_variable_name(monkeypatch):
    # "nan" parses as a float and compares false with everything, so "<= 0" let it through.
    monkeypatch.setenv("RAG_AUDIT_CHAIN_PERSIST_INTERVAL_SECONDS", "nan")

    with pytest.raises(ValueError, match=r"\(RAG_AUDIT_CHAIN_PERSIST_INTERVAL_SECONDS\) must be a finite number"):
        Settings.from_env()


@pytest.mark.parametrize(
    "field",
    ["vector_store_url", "embedding_base_url", "reranker_base_url", "jwt_jwks_url", "otel_exporter_otlp_endpoint"],
)
@pytest.mark.parametrize("value", ["qdrant:6333", "ftp://qdrant", "http://", "qdrant.vector.svc"])
def test_endpoints_must_be_http_urls(field, value):
    with pytest.raises(ValueError, match=f"{field} must be an http:// or https:// URL with a host"):
        Settings(document_dir=DOCS, **{field: value})


def test_well_formed_endpoints_are_accepted():
    settings = Settings(
        document_dir=DOCS,
        retrieval_backend="qdrant",
        vector_store_url="http://qdrant.vector.svc.cluster.local:6333",
        embedding_provider="openai-compatible",
        embedding_base_url="https://embeddings.example/v1",
        embedding_model="bge-small",
    )

    assert settings.vector_store_url.startswith("http://")


def test_the_redis_head_store_needs_a_redis_url():
    with pytest.raises(ValueError, match="audit_chain_store_redis_url must be a redis://"):
        Settings(document_dir=DOCS, audit_chain_store_backend="redis", audit_chain_store_redis_url="budget-redis:6379")

    Settings(document_dir=DOCS, audit_chain_store_backend="redis", audit_chain_store_redis_url="rediss://redis:6380/0")


def test_errors_from_the_environment_name_every_variable_involved(monkeypatch):
    monkeypatch.setenv("DEFAULT_TOP_K", "5")
    monkeypatch.setenv("MAX_TOP_K", "2")

    with pytest.raises(ValueError) as raised:
        Settings.from_env()

    assert str(raised.value) == "default_top_k (DEFAULT_TOP_K) must not exceed max_top_k (MAX_TOP_K)"


def test_alternative_variable_names_are_listed(monkeypatch):
    monkeypatch.setenv("RAG_RETRIEVAL_BACKEND", "qdrant")
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.delenv("VECTOR_STORE_URL", raising=False)

    with pytest.raises(ValueError, match=r"vector_store_url \(QDRANT_URL or VECTOR_STORE_URL\) must be set"):
        Settings.from_env()


def test_an_invalid_default_sandbox_names_its_variable(monkeypatch):
    monkeypatch.setenv("DEFAULT_SANDBOX_ID", "Local Lab")

    with pytest.raises(ValueError, match=r"default_sandbox_id \(DEFAULT_SANDBOX_ID\): sandbox id must be"):
        Settings.from_env()


def test_env_by_field_matches_what_from_env_reads():
    source = Path(settings_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    reader = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_from_env")
    call = next(
        node for node in ast.walk(reader) if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "cls"
    )
    read: dict[str, tuple[str, ...]] = {}
    for keyword in call.keywords:
        names = tuple(
            node.value
            for node in ast.walk(keyword.value)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_]+", node.value)
        )
        read[keyword.arg] = names

    assert read == ENV_BY_FIELD
