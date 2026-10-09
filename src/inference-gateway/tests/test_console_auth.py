# ruff: noqa: F811  (the shared auth_gateway fixture is imported, then requested by name)
import base64
import hashlib
import json
import logging
from dataclasses import replace
from time import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from app.budget import BudgetBackendError
from app.console_auth import redact_auth_query
from app.jwt_auth import JwtVerifier
from app.main import create_app
from app.ratelimit import InMemoryRateLimiter
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


@pytest.mark.parametrize(
    "path,method", [("session", "POST"), ("login", "GET"), ("callback", "GET"), ("logout", "POST")]
)
def test_sign_in_throttle_cannot_be_bypassed_by_team_or_forwarded_headers(auth_gateway, path, method):
    _, _, _, build = auth_gateway
    client, app = build(rate_limit_enabled=True, rate_limit_requests_per_window=2)
    app.state.rate_limiter = InMemoryRateLimiter(app.state.settings)
    for index in range(3):
        response = client.request(
            method, f"/v1/auth/{path}", json={"key": "invalid"} if path == "session" else None,
            headers={"X-Sandbox-ID": f"team-{index}", "X-Forwarded-For": f"192.0.2.{index}"},
        )
        assert (response.status_code == 429) is (index == 2)
    assert int(response.headers["retry-after"]) > 0
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/v1/auth/config").status_code == 200


def test_sign_in_throttle_fails_closed_on_store_outage(auth_gateway, monkeypatch):
    _, _, _, build = auth_gateway
    client, app = build(rate_limit_enabled=True, rate_limit_fail_open=True)

    def unavailable(key):
        raise BudgetBackendError("unavailable")

    monkeypatch.setattr(app.state.rate_limiter, "check", unavailable)
    response = client.post("/v1/auth/session", json={"key": "admin"})
    assert response.status_code == 503
    assert "retry-after" in response.headers
    assert not client.cookies.get(SESSION_COOKIE)


def test_access_log_omits_oidc_codes_and_state():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/v1/auth/callback?code=private-code&state=private-state", "1.1", 303), None,
    )
    assert redact_auth_query(record)
    assert "/v1/auth/callback" in record.getMessage()
    assert "private-code" not in record.getMessage()
    assert "private-state" not in record.getMessage()


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


def test_oidc_session_names_the_person_from_their_id_token(oidc):
    client, _, _, fixture, start = oidc
    fixture["claims"] = {"name": "Alice Moreau", "email": "alice@example.com"}
    client.get("/v1/auth/callback", params=start(), follow_redirects=False)
    assert client.get("/v1/auth/session").json()["principal"]["name"] == "Alice Moreau"


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


def test_browser_callback_failures_return_to_the_console(oidc):
    client, _, _, fixture, start = oidc
    html = {"accept": "text/html,application/xhtml+xml"}
    params = start()
    response = client.get(
        "/v1/auth/callback", params={**params, "state": "wrong"}, headers=html, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/console/?signin_error=expired"
    params = start()
    response = client.get(
        "/v1/auth/callback", params={**params, "error": "access_denied"}, headers=html, follow_redirects=False
    )
    assert response.headers["location"] == "/console/?signin_error=declined"
    fixture["claims"] = {"team": "unknown"}
    response = client.get("/v1/auth/callback", params=start(), headers=html, follow_redirects=False)
    assert response.headers["location"] == "/console/?signin_error=rejected"
    assert not client.cookies.get(SESSION_COOKIE)


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


def test_browser_sees_unavailable_when_signing_keys_are_down(oidc):
    client, _, _, fixture, start = oidc
    fixture["jwks_status"] = 503
    response = client.get(
        "/v1/auth/callback", params=start(), headers={"accept": "text/html"}, follow_redirects=False
    )
    assert response.headers["location"] == "/console/?signin_error=unavailable"


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


@pytest.mark.parametrize("role", ["admin", "builder", "approver", "viewer"])
def test_group_mapping_uses_exact_team_scoped_groups_instead_of_role_claim(oidc, role):
    client, app, store, fixture, start = oidc
    app.state.settings = replace(app.state.settings, oidc_groups_claim="company.groups", oidc_group_role_mappings={
        "team": {"Engineering": role, "Other matching group": role}, "other": {"Unrelated": "admin"},
    })
    fixture["claims"] = {
        "company.groups": ["Unrelated", "Engineering", "Engineering", "Other matching group"],
        "role": "invalid-and-ignored", "project": "private",
    }
    response = client.get("/v1/auth/callback", params=start(), follow_redirects=False)
    assert response.status_code == 303
    identity = client.get("/v1/team").json()
    assert identity["role"] == role and identity["projects"] == ["private"]
    assert client.get("/v1/team", headers={"X-Sandbox-ID": "other"}).status_code == 403
    assert client.get("/v1/team/sso").status_code == 403
    assert "Engineering" not in repr(store.data)


@pytest.mark.parametrize("groups", [None, "builders", {}, 123, [], [None], [1], [""], [" "],
                                    ["Builders"], [" builders"], ["unmapped"], ["builders", "reviewers"]])
def test_group_mapping_rejects_missing_malformed_unmapped_and_conflicting_groups(oidc, groups):
    client, app, _, fixture, start = oidc
    app.state.settings = replace(app.state.settings, oidc_group_role_mappings={
        "team": {"builders": "builder", "reviewers": "approver"},
    })
    fixture["claims"] = {"role": "admin"}
    if groups is not None:
        fixture["claims"]["groups"] = groups
    response = client.get("/v1/auth/callback", params=start(), follow_redirects=False)
    assert response.status_code == 401
    assert not client.cookies.get(SESSION_COOKIE)


def test_group_mapping_does_not_fall_back_for_an_unmapped_team(oidc):
    client, app, _, fixture, start = oidc
    app.state.settings = replace(app.state.settings, oidc_group_role_mappings={"other": {"admins": "admin"}})
    fixture["claims"] = {"role": "admin", "groups": ["admins"]}
    assert client.get("/v1/auth/callback", params=start(), follow_redirects=False).status_code == 401


@pytest.mark.parametrize("claims", [{"team": "unknown"}, {"project": "unknown"}, {"project": []}])
def test_mapped_groups_still_require_an_existing_team_and_project(oidc, claims):
    client, app, _, fixture, start = oidc
    app.state.settings = replace(app.state.settings, oidc_group_role_mappings={
        "team": {"admins": "admin"}, "unknown": {"admins": "admin"},
    })
    fixture["claims"] = {"groups": ["admins"], **claims}
    assert client.get("/v1/auth/callback", params=start(), follow_redirects=False).status_code == 401


def test_bearer_jwt_and_new_jwt_sessions_keep_their_own_authorization(oidc, signing_key):
    client, app, _, _, _ = oidc
    app.state.settings = replace(
        app.state.settings, oidc_group_role_mappings={"team": {"builders": "builder"}},
        jwt_auth_enabled=True, jwt_jwks_url="https://idp.example/keys", jwt_issuer="https://idp.example",
        jwt_audience="automation",
    )
    app.state.jwt_verifier = JwtVerifier(app.state.settings)
    token = jwt.encode({
        "iss": "https://idp.example", "aud": "automation", "sub": "bot", "exp": int(time()) + 300,
        "team": "team", "role": "admin",
    }, signing_key, algorithm="RS256", headers={"kid": "current"})
    assert client.get("/v1/team/sso", headers=auth(token)).status_code == 200
    login(client, token)
    assert client.get("/v1/team/sso").status_code == 200


def test_group_session_expires_with_id_token_and_policy_changes_require_sign_in(oidc):
    client, app, store, fixture, start = oidc
    original = app.state.settings
    app.state.settings = replace(original, oidc_group_role_mappings={"team": {"builders": "builder"}})
    expiry = int(time()) + 120
    fixture["claims"] = {"groups": ["builders"], "exp": expiry}
    assert client.get("/v1/auth/callback", params=start(), follow_redirects=False).status_code == 303
    assert client.get("/v1/auth/session").json()["absolute_expires_at"] == expiry
    app.state.settings = replace(app.state.settings, oidc_group_role_mappings={
        "team": {"builders": "builder"}, "other": {"reviewers": "approver"},
    })
    assert client.get("/v1/team").status_code == 200
    app.state.settings = replace(app.state.settings, oidc_group_role_mappings={"team": {"builders": "viewer"}})
    assert client.get("/v1/team").status_code == 401
    assert client.get("/v1/auth/callback", params=start(), follow_redirects=False).status_code == 303
    assert client.get("/v1/team").json()["role"] == "viewer"
    store.now = expiry
    assert client.get("/v1/auth/session").status_code == 401


@pytest.mark.parametrize("legacy", [False, True])
def test_enabling_group_mapping_invalidates_existing_oidc_sessions(oidc, legacy):
    client, app, store, fixture, start = oidc
    fixture["claims"] = {"role": "admin"}
    assert client.get("/v1/auth/callback", params=start(), follow_redirects=False).status_code == 303
    if legacy:
        for key, raw in store.data.items():
            if ":session:" in key:
                session = json.loads(raw)
                session.pop("oidc_policy_digest")
                store.data[key] = json.dumps(session)
    app.state.settings = replace(app.state.settings, oidc_group_role_mappings={"team": {"builders": "builder"}})
    assert client.get("/v1/team").status_code == 401
    login(client)
    assert client.get("/v1/team/sso").status_code == 200


def test_sso_configuration_is_private_admin_only_and_team_scoped(oidc):
    client, app, _, _, _ = oidc
    app.state.settings = replace(app.state.settings, oidc_group_role_mappings={
        "team": {"builders": "builder"}, "other": {"secret-other-group": "admin"},
    })
    response = client.get("/v1/team/sso", headers=auth("admin"))
    assert response.status_code == 200
    assert response.json() == {
        "team_id": "team", "enabled": True, "provider_name": "idp.example", "role_source": "groups",
        "team_claim": "team", "project_claim": "project", "role_claim": None, "default_role": None,
        "groups_claim": "groups", "group_role_mappings": {"builders": "builder"},
    }
    assert "client-secret" not in response.text and "secret-other-group" not in response.text
    assert "no-store" in response.headers["cache-control"]
    assert "builders" not in client.get("/v1/auth/config").text
    assert client.get("/v1/team/sso").status_code == 401
    for role in ("viewer", "builder", "approver"):
        assert client.get("/v1/team/sso", headers=auth(role)).status_code == 403
    scoped = create_key(client, role="admin", project="private")
    assert client.get("/v1/team/sso", headers=auth(scoped["key"])).status_code == 403
    app.state.settings = replace(app.state.settings, oidc_group_role_mappings={})
    info = client.get("/v1/team/sso", headers=auth("admin")).json()
    assert info["role_source"] == "claim" and info["default_role"] == "viewer"
    assert info["groups_claim"] is None and info["group_role_mappings"] == {}


@pytest.mark.parametrize("mapping", [[], None, {"Team": {}}, {"bad team": {}}, {"team": []},
                                     {"team": {"": "viewer"}}, {"team": {"  ": "viewer"}},
                                     {"team": {"group": "owner"}}, {"team": {"group": ["admin"]}}])
def test_invalid_group_mapping_configuration_fails_at_startup(oidc, mapping):
    _, app, _, _, _ = oidc
    with pytest.raises(ValueError):
        replace(app.state.settings, oidc_group_role_mappings=mapping)


def test_group_mapping_environment_and_required_issuer(monkeypatch):
    for name, value in {
        "OIDC_ISSUER": "https://idp.example", "OIDC_CLIENT_ID": "console",
        "OIDC_REDIRECT_URL": "https://gateway.example/v1/auth/callback", "OIDC_TEAM_CLAIM": "team",
        "SANDBOX_BUDGET_BACKEND": "redis", "OIDC_GROUPS_CLAIM": "memberOf",
        "OIDC_GROUP_ROLE_MAPPINGS": '{"team":{"Reviewers":"approver"}}',
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.oidc_groups_claim == "memberOf"
    assert settings.oidc_group_role_mappings == {"team": {"Reviewers": "approver"}}
    with pytest.raises(ValueError, match="requires OIDC_ISSUER"):
        replace(settings, oidc_issuer="")
    with pytest.raises(ValueError, match="OIDC_GROUPS_CLAIM"):
        replace(settings, oidc_groups_claim=" ")
    monkeypatch.setenv("OIDC_GROUP_ROLE_MAPPINGS", "not-json")
    with pytest.raises(ValueError):
        Settings.from_env()
