"""Tests for the opt-in bundled team console."""

from __future__ import annotations

import re

from app.main import create_app
from app.settings import Settings
from fastapi.testclient import TestClient


def _settings(**overrides):
    base = {
        "runtime_backend": "ollama",
        "ollama_base_url": "http://o:11434",
        "vllm_base_url": "http://v:8000",
        "model_id": "m",
        "request_timeout_seconds": 5,
    }
    base.update(overrides)
    return Settings(**base)


def test_console_served_when_enabled():
    client = TestClient(create_app(_settings(admin_console_enabled=True)))
    resp = client.get("/console/")
    assert resp.status_code == 200
    assert "AgentWorkflows Console" in resp.text
    assert "text/html" in resp.headers["content-type"]
    assert resp.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assets = re.findall(r'(?:src|href)="(/console/assets/[^"]+)"', resp.text)
    assert assets
    for path in assets:
        asset = client.get(path)
        assert asset.status_code == 200
        assert asset.headers["x-content-type-options"] == "nosniff"


def test_console_not_mounted_when_disabled():
    client = TestClient(create_app(_settings(admin_console_enabled=False)))
    assert client.get("/console/").status_code == 404


def test_console_is_unauthenticated_even_with_api_key_auth():
    # The static page must load without a key even when the API requires auth; the /v1 calls it
    # makes from the browser carry the operator's key and are governed normally.
    client = TestClient(
        create_app(_settings(admin_console_enabled=True, api_key_auth_enabled=True, api_key_sha256s=("a" * 64,)))
    )
    assert client.get("/console/").status_code == 200
