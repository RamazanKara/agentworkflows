# ruff: noqa: F811  (the shared auth_gateway fixture is imported, then requested by name)
import base64
import hashlib
import json
from dataclasses import replace
from time import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from app.main import create_app
from app.sessions import CSRF_COOKIE, IDLE_SECONDS, MAX_SECONDS, SESSION_COOKIE
from app.settings import Settings
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tests.gateway_support import _tool_settings
from tests.test_managed_keys import auth_gateway, create_key  # noqa: F401
from tests.test_teams import auth


def login(client, key="admin"):
    response = client.post("/v1/auth/session", json={"key": key})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_session_restore_cookie_flags_and_logout(auth_gateway):
    client, _, store, build = auth_gateway
    response = client.post("/v1/auth/session", json={"key": "admin"})
    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert "HttpOnly" in cookies[0] and "Secure" in cookies[0] and "SameSite=lax" in cookies[0]
    assert "HttpOnly" not in cookies[1]
    assert "no-store" in response.headers["cache-control"]
    cookie = client.cookies.get(SESSION_COOKIE)
    assert cookie not in repr(store.data)
    assert '"credential_digest"' in repr(store.data)
    replica, _ = build()
    replica.cookies.update(client.cookies)
    restored = replica.get("/v1/auth/session")
    assert restored.json()["principal"]["key_id"] == "admin"
    assert restored.json()["csrf_token"] == response.json()["csrf_token"]
    assert replica.get("/v1/team").json()["team_id"] == "team"
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}
    assert client.post("/v1/auth/logout", headers=headers).status_code == 200
    assert not client.cookies.get(SESSION_COOKIE)
    assert replica.get("/v1/auth/session").status_code == 401


@pytest.mark.parametrize("case", ["missing_header", "wrong_header", "missing_cookie", "wrong_cookie"])
def test_cookie_writes_require_double_submit_csrf(auth_gateway, case):
    client, _, _, _ = auth_gateway
    headers = login(client)
    if case == "missing_header":
        headers = {}
    elif case == "wrong_header":
        headers = {"X-CSRF-Token": "wrong"}
    else:
        client.cookies.delete(CSRF_COOKIE)
        if case == "wrong_cookie":
            client.cookies.set(CSRF_COOKIE, "wrong", domain="localhost.local", path="/")
    for path, body in (
        ("/v1/team/keys", {"name": "denied"}), ("/v1/auth/logout", None),
        ("/v1/auth/session", {"key": "viewer"}),
    ):
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == 403
        assert response.json()["detail"]["reason"] == "csrf_invalid"


def test_bearer_and_x_api_key_do_not_require_csrf_or_fall_back_to_cookie(auth_gateway):
    client, _, _, _ = auth_gateway
    login(client)
    assert client.post("/v1/team/keys", headers=auth("admin"), json={"name": "Bearer"}).status_code == 201
    assert client.post("/v1/team/keys", headers={"X-API-Key": "admin"}, json={"name": "Header"}).status_code == 201
    assert client.get("/v1/team", headers=auth("invalid")).status_code == 401
    assert client.get("/v1/team", headers={"X-Sandbox-ID": "other"}).status_code == 403


def test_viewers_can_log_out_and_rotate_identity_but_cannot_manage_keys(auth_gateway):
    client, _, _, build = auth_gateway
    headers = login(client, "viewer")
    old, _ = build()
    old.cookies.update(client.cookies)
    assert client.post("/v1/team/keys", headers=headers, json={"name": "denied"}).status_code == 403
    replacement = client.post("/v1/auth/session", headers=headers, json={"key": "admin"})
    assert replacement.status_code == 200
    assert old.get("/v1/auth/session").status_code == 401
    assert client.get("/v1/team").json()["role"] == "admin"
    assert client.post("/v1/auth/logout", headers={"X-CSRF-Token": replacement.json()["csrf_token"]}).status_code == 200
    headers = login(client, "viewer")
    assert client.post("/v1/auth/logout", headers=headers).status_code == 200


def test_session_self_revoke_and_demotion_are_rejected(auth_gateway):
    client, _, _, _ = auth_gateway
    key = create_key(client, role="admin")
    headers = login(client, key["key"])
    path = f"/v1/team/keys/{key['key_id']}"
    assert client.delete(path, headers=headers).status_code == 409
    assert client.patch(path, headers=headers, json={"role": "viewer"}).status_code == 409


def test_sessions_slide_for_twelve_hours_and_stop_after_seven_days(auth_gateway):
    client, _, store, _ = auth_gateway
    login(client)
    started = store.now
    store.now += IDLE_SECONDS - 60
    assert client.get("/v1/auth/session").status_code == 200
    assert max(store.expires.values()) == store.now + IDLE_SECONDS
    for _ in range(13):
        store.now += IDLE_SECONDS - 60
        assert client.get("/v1/team").status_code == 200
    assert max(store.expires.values()) <= started + MAX_SECONDS
    store.now = started + MAX_SECONDS
    assert client.get("/v1/auth/session").status_code == 401
    client.cookies.clear()
    login(client)
    store.now += IDLE_SECONDS
    assert client.get("/v1/auth/session").status_code == 401


def test_managed_key_expiry_invalidates_its_session(auth_gateway):
    client, _, store, _ = auth_gateway
    key = create_key(client, expires_at=store.now + 60)
    login(client, key["key"])
    store.now += 60
    assert client.get("/v1/auth/session").status_code == 401


def test_sign_in_rejects_cross_site_posts_and_local_cookie_configuration(auth_gateway):
    client, _, _, build = auth_gateway
    for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert client.post("/v1/auth/session", headers=headers, json={"key": "admin"}).status_code == 403
    local, app = build(session_cookie_secure=False)
    local.base_url = "http://localhost"
    response = local.post("/v1/auth/session", json={"key": "admin"})
    assert response.status_code == 200 and "Secure" not in response.headers["set-cookie"]
    remote = TestClient(app, base_url="http://remote.example")
    assert remote.post("/v1/auth/session", json={"key": "admin"}).status_code == 503


def test_sign_in_behind_tls_ingress_and_whitespace_key(auth_gateway):
    _, app, _, _ = auth_gateway
    client = TestClient(app, base_url="http://gateway.example")
    response = client.post(
        "/v1/auth/session", headers={"Origin": "https://gateway.example"}, json={"key": "admin"}
    )
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
    assert client.post("/v1/auth/session", headers=auth("admin"), json={"key": " "}).status_code == 401


def test_pasted_jwt_session_cannot_outlive_the_token(auth_gateway):
    _, _, store, build = auth_gateway
    client, app = build(jwt_auth_enabled=True, jwt_jwks_url="https://idp.example/keys", jwt_tenant_claim="team")
    app.state.jwt_verifier.verify = AsyncMock(return_value={
        "sub": "alice", "team": "team", "role": "viewer", "exp": store.now + 60,
    })
    login(client, "signed.jwt.token")
    assert "signed.jwt.token" not in repr(store.data)
    assert client.get("/v1/team").json()["role"] == "viewer"
    store.now += 60
    assert client.get("/v1/auth/session").status_code == 401


def test_sign_in_is_off_by_default():
    client = TestClient(create_app(_tool_settings()))
    assert client.get("/v1/auth/config").json() == {
        "api_key": False, "jwt": False,
        "oidc": {"enabled": False, "provider_name": None, "login_url": None},
    }
    assert client.post("/v1/auth/session", json={"key": "admin"}).status_code == 404
    assert client.get("/v1/auth/login").status_code == 404


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc(auth_gateway, monkeypatch, signing_key):
    _, _, store, build = auth_gateway
    client, app = build(
        oidc_issuer="https://idp.example", oidc_client_id="console", oidc_client_secret="client-secret",
        oidc_redirect_url="https://localhost/v1/auth/callback", jwt_tenant_claim="team",
    )
    fixture = {
        "claims": {}, "nonce": "", "challenge": "", "algorithm": "RS256",
        "jwks_status": 200, "exchange_count": 0,
    }
    public_key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    public_key.update(kid="current", alg="RS256")

    def respond(request):
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": "https://idp.example", "authorization_endpoint": "https://idp.example/authorize",
                "token_endpoint": "https://idp.example/token", "jwks_uri": "https://idp.example/keys",
                "code_challenge_methods_supported": ["S256"],
            })
        if request.url.path == "/keys":
            return httpx.Response(fixture["jwks_status"], json={"keys": [public_key]})
        assert request.url.path == "/token"
        fixture["exchange_count"] += 1
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["authorization_code"]
        assert form["redirect_uri"] == ["https://localhost/v1/auth/callback"]
        digest = hashlib.sha256(form["code_verifier"][0].encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert challenge == fixture["challenge"]
        if app.state.settings.oidc_client_secret:
            assert request.headers["authorization"].startswith("Basic ")
        else:
            assert "authorization" not in request.headers and "client_secret" not in form
        claims = {
            "iss": "https://idp.example", "aud": "console", "sub": "alice", "iat": int(time()),
            "exp": int(time()) + 300, "nonce": fixture["nonce"], "team": "team", **fixture["claims"],
        }
        for name in fixture.get("omit", []):
            claims.pop(name, None)
        key = signing_key if fixture["algorithm"] == "RS256" else "fake-secret-with-at-least-32-bytes"
        token = jwt.encode(claims, key, algorithm=fixture["algorithm"], headers={"kid": "current"})
        if fixture.get("bad_signature"):
            token = token[:-8] + "AAAAAAAA"
        return httpx.Response(200, json={"id_token": token, "access_token": "must-not-be-stored"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(**{**kwargs, "transport": httpx.MockTransport(respond)})
    )

    def start():
        response = client.get("/v1/auth/login", follow_redirects=False)
        assert response.status_code == 302
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["code_challenge_method"] == ["S256"]
        assert query["response_type"] == ["code"]
        fixture["nonce"], fixture["challenge"] = query["nonce"][0], query["code_challenge"][0]
        return {"state": query["state"][0], "code": "authorization-code"}

    return client, app, store, fixture, start


def test_oidc_code_pkce_nonce_session_and_public_configuration(oidc):
    client, _, store, _, start = oidc
    config = client.get("/v1/auth/config")
    assert config.json()["oidc"]["enabled"]
    assert "client-secret" not in config.text
    response = client.get("/v1/auth/callback", params=start(), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/console/"
    session = client.get("/v1/auth/session").json()
    assert session["principal"]["sub"] == "alice"
    assert session["principal"]["role"] == "viewer"
    assert client.get("/v1/team").json()["team_id"] == "team"
    assert "must-not-be-stored" not in repr(store.data)
    assert "client-secret" not in repr(store.data)


def test_public_oidc_client_and_custom_claim_mapping(oidc):
    client, app, _, fixture, start = oidc
    app.state.settings = replace(app.state.settings, oidc_client_secret="", oidc_team_claim="tenant",
                                 oidc_role_claim="access", oidc_project_claim="workspace")
    fixture["claims"] = {"tenant": "team", "access": "builder", "workspace": "private"}
    assert client.get("/v1/auth/callback", params=start(), follow_redirects=False).status_code == 303
    assert client.get("/v1/team").json()["projects"] == ["private"]
    assert client.get("/v1/team").json()["role"] == "builder"


def test_state_mismatch_browser_binding_replay_and_expiry(oidc):
    client, app, store, fixture, start = oidc
    params = start()
    assert client.get("/v1/auth/callback", params={**params, "state": "wrong"}).status_code == 400
    stranger = TestClient(app, base_url="https://localhost")
    assert stranger.get("/v1/auth/callback", params=params).status_code == 400
    assert fixture["exchange_count"] == 0
    assert client.get("/v1/auth/callback", params=params, follow_redirects=False).status_code == 303
    assert client.get("/v1/auth/callback", params=params).status_code == 400
    assert fixture["exchange_count"] == 1
    params = start()
    store.now += 601
    assert client.get("/v1/auth/callback", params=params).status_code == 400
    assert fixture["exchange_count"] == 1


@pytest.mark.parametrize("claims", [
    {"iss": "https://wrong.example"}, {"aud": "other"}, {"exp": 1}, {"nonce": "wrong"},
    {"sub": ""}, {"iat": "invalid"}, {"exp": "9999999999"}, {"nbf": 9999999999},
    {"role": "owner"}, {"role": None},
    {"team": "unknown"}, {"team": None}, {"project": "unknown"}, {"project": []},
    {"aud": ["console", "other"]}, {"azp": "other"},
])
def test_id_token_validation_failures(oidc, claims):
    client, _, _, fixture, start = oidc
    fixture["claims"] = claims
    response = client.get("/v1/auth/callback", params=start(), follow_redirects=False)
    assert response.status_code == 401
    assert not client.cookies.get(SESSION_COOKIE)


@pytest.mark.parametrize("field", ["sub", "iat", "exp", "nonce"])
def test_required_id_token_claims(oidc, field):
    client, _, _, fixture, start = oidc
    fixture["omit"] = [field]
    assert client.get("/v1/auth/callback", params=start(), follow_redirects=False).status_code == 401


@pytest.mark.parametrize("failure", ["signature", "algorithm", "jwks"])
def test_bad_signature_algorithm_and_jwks_outage(oidc, failure):
    client, _, _, fixture, start = oidc
    if failure == "signature":
        fixture["bad_signature"] = True
    elif failure == "algorithm":
        fixture["algorithm"] = "HS256"
    else:
        fixture["jwks_status"] = 503
    response = client.get("/v1/auth/callback", params=start(), follow_redirects=False)
    assert response.status_code == (503 if failure == "jwks" else 401)
    assert not client.cookies.get(SESSION_COOKIE)


def test_oidc_settings_from_environment_and_secret_repr(monkeypatch):
    for name, value in {
        "OIDC_ISSUER": "https://idp.example", "OIDC_CLIENT_ID": "console",
        "OIDC_CLIENT_SECRET": "never-print-me", "OIDC_REDIRECT_URL": "https://gateway.example/v1/auth/callback",
        "JWT_TENANT_CLAIM": "team", "SANDBOX_BUDGET_BACKEND": "redis",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.oidc_default_role == "viewer" and settings.oidc_scopes == "openid profile email"
    assert "never-print-me" not in repr(settings)
    with pytest.raises(ValueError, match="HTTPS"):
        replace(settings, oidc_issuer="http://remote.example")
    with pytest.raises(ValueError, match="OIDC requires"):
        replace(settings, oidc_client_id="")
    with pytest.raises(ValueError, match="SANDBOX_BUDGET_BACKEND"):
        replace(settings, sandbox_budget_backend="memory")
