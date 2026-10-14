"""Tests for the opt-in bundled team console."""

from __future__ import annotations

import gzip
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


def _console_assets(client):
    page = client.get("/console/")
    return page, re.findall(r'(?:src|href)="(/console/assets/[^"]+)"', page.text)


def test_hashed_console_assets_are_cached_and_the_page_is_not():
    # Vite content-hashes every asset name, so a cached copy can never go stale; the page
    # naming the current hashes must always be revalidated.
    client = TestClient(create_app(_settings(admin_console_enabled=True)))
    page, assets = _console_assets(client)
    assert page.headers["cache-control"] == "no-store"
    for path in assets:
        asset = client.get(path)
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert "frame-ancestors 'none'" in asset.headers["content-security-policy"]
        revalidated = client.get(path, headers={"If-None-Match": asset.headers["etag"]})
        assert revalidated.status_code == 304
        assert revalidated.headers["cache-control"] == "public, max-age=31536000, immutable"
    # A missing asset is a 404 that must not be cached for a year.
    missing = client.get("/console/assets/index-missing.js")
    assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"


def test_console_assets_are_gzipped_only_when_the_client_accepts_it():
    client = TestClient(create_app(_settings(admin_console_enabled=True)))
    _, assets = _console_assets(client)
    script = next(path for path in assets if path.endswith(".js"))
    plain = client.get(script, headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    with client.stream("GET", script, headers={"Accept-Encoding": "gzip"}) as gzipped:
        wire = b"".join(gzipped.iter_raw())
    assert gzipped.headers["content-encoding"] == "gzip"
    assert gzipped.headers["vary"] == "Accept-Encoding"
    assert len(wire) < len(plain.content) / 2
    assert gzip.decompress(wire) == plain.content
    font = next((path for path in assets if path.endswith(".woff2")), None)
    if font is not None:  # already compressed: sent as is
        assert "content-encoding" not in client.get(font, headers={"Accept-Encoding": "gzip"}).headers


def test_event_streams_are_never_gzipped_or_buffered():
    from tests.gateway_support import FakeRuntimeClient
    from tests.test_teams import asgi_post

    app = create_app(_settings(allow_streaming=True))
    chunks = [b'data: {"choices":[{"delta":{"content":"%d"}}]}\n\n' % index for index in range(200)]
    app.state.runtime_client = FakeRuntimeClient(stream_chunks=[*chunks, b"data: [DONE]\n\n"])
    sent = []

    async def send(message):
        sent.append(message)

    body = {"stream": True, "messages": [{"role": "user", "content": "hi"}]}
    asgi_post(app, "/v1/chat/completions", body, {"Accept-Encoding": "gzip"}, send)
    headers = {name.decode(): value.decode() for name, value in sent[0]["headers"]}
    assert headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in headers
    bodies = [message.get("body", b"") for message in sent[1:]]
    assert len([body for body in bodies if body]) > 100  # forwarded chunk by chunk, not buffered
    assert b"".join(bodies).endswith(b"data: [DONE]\n\n")
