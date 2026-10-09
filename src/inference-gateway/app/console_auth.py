"""Console sign-in through API credentials or the configured OpenID provider."""

import base64
import hashlib
import json
import logging
import secrets
from contextlib import suppress
from math import isfinite
from time import time
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app.jwks import JwksCache, JwksUnavailableError, JwtAuthError, JwtConfig, JwtVerifierCore
from app.oidc_access import access_policy_digest, group_role
from app.request_context import _jwt_principal, authenticate_credential
from app.sessions import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    bind_session,
    check_csrf,
    check_origin,
    cookie_options,
    create_session,
    load_session,
    refresh_cookies,
    same_token,
    session_info,
    session_key,
)
from app.settings import validate_sandbox_id
from app.workflow_budget import redis_call

AUTH_PATHS = {f"/v1/auth/{name}" for name in ("config", "login", "callback", "logout", "session", "invitations/accept")}
STATE_COOKIE = "aw_oidc_state"
SIGNIN_ERRORS = {400: "expired", 401: "rejected"}


class SessionLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=16384, repr=False)


def require_sessions(request: Request) -> None:
    settings = request.app.state.settings
    if not (settings.admin_console_enabled or settings.oidc_issuer):
        raise HTTPException(404, detail="Console sign-in is not enabled.")


async def provider(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if not settings.oidc_issuer:
        raise HTTPException(404, detail="Company sign-in is not configured.")
    cached = getattr(request.app.state, "oidc_provider", None)
    if cached and cached[0] > time():
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=min(settings.request_timeout_seconds, 10)) as client:
            result = await client.get(settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration")
            result.raise_for_status()
            data = result.json()
        if not isinstance(data, dict) or data.get("issuer") != settings.oidc_issuer:
            raise ValueError("issuer mismatch")
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(data[name], str):
                raise ValueError("invalid provider endpoint")
            url = urlsplit(data[name])
            local = url.hostname in {"localhost", "127.0.0.1", "::1"}
            if not url.hostname or url.fragment or url.username or url.password or (
                url.scheme != "https" and not (local and url.scheme == "http")
            ):
                raise ValueError("invalid provider endpoint")
        if "S256" not in data.get("code_challenge_methods_supported", ["S256"]):
            raise ValueError("PKCE S256 is required")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            503, detail="Company sign-in is unavailable. Check the OIDC provider configuration."
        ) from exc
    config = JwtConfig(
        True, data["jwks_uri"], settings.oidc_issuer, settings.oidc_client_id,
        settings.jwt_cache_seconds, settings.request_timeout_seconds,
    )
    request.app.state.oidc_verifier = JwtVerifierCore(config, JwksCache(config))
    request.app.state.oidc_provider = (time() + settings.jwt_cache_seconds, data)
    return data


async def oidc_identity(request: Request, token: str, nonce: str) -> None:
    settings = request.app.state.settings
    try:
        if jwt.get_unverified_header(token).get("alg") not in {"RS256", "ES256"}:
            raise JwtAuthError("unsupported ID token algorithm")
        claims = await request.app.state.oidc_verifier.verify(token)
        if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
            raise JwtAuthError("missing subject")
        for field in ("iat", "exp"):
            value = claims.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
                raise JwtAuthError("invalid ID token timestamp")
        if not isinstance(claims.get("nonce"), str) or not same_token(claims["nonce"], nonce):
            raise JwtAuthError("nonce mismatch")
        multiple = isinstance(claims["aud"], list) and len(claims["aud"]) > 1
        if (multiple or "azp" in claims) and claims.get("azp") != settings.oidc_client_id:
            raise JwtAuthError("authorized party mismatch")
        team = claims.get(settings.oidc_team_claim or settings.jwt_tenant_claim)
        if not isinstance(team, str):
            raise JwtAuthError("invalid team claim")
        team = validate_sandbox_id(team)
        role = claims.get(settings.oidc_role_claim, settings.oidc_default_role)
        if settings.oidc_group_role_mappings:
            role = group_role(settings, team, claims)
        project = claims.get(settings.oidc_project_claim)
        if not isinstance(role, str) or role not in {"admin", "builder", "approver", "viewer"}:
            raise JwtAuthError("invalid membership claims")
        if project is not None:
            if not isinstance(project, str):
                raise JwtAuthError("invalid project claim")
            project = validate_sandbox_id(project)
        policy = request.app.state.sandbox_policy_set.policies.get(team)
        if policy is None or (project is not None and project not in policy.projects):
            raise JwtAuthError("unknown team or project")
    except JwksUnavailableError as exc:
        raise HTTPException(503, detail="Company sign-in keys are temporarily unavailable.") from exc
    except (JwtAuthError, jwt.PyJWTError, ValueError, KeyError, TypeError, OverflowError) as exc:
        raise HTTPException(
            401, detail="Company sign-in token was rejected. Sign in again or contact your admin."
        ) from exc
    request.state.principal = _jwt_principal({**claims, "role": role, "project": project})
    # The console shows who is signed in and approvals name the reviewer.
    display = claims.get("name") or claims.get("email")
    if isinstance(display, str) and display.strip():
        request.state.principal["name"] = display.strip()[:128]
    request.state.sandbox_id = team
    request.state.sandbox_bound = True
    request.state.oidc_policy_digest = access_policy_digest(settings, team)
    # Group membership is a sign-in snapshot; require a fresh ID token when it expires.
    if settings.oidc_group_role_mappings:
        request.state.credential_expires_at = claims["exp"]


def redact_auth_query(record: logging.LogRecord) -> bool:
    # Uvicorn's access logger includes the authorization code and state in callback URLs.
    if isinstance(record.args, tuple) and len(record.args) == 5:
        peer, method, target, version, status = record.args
        if isinstance(target, str) and target.partition("?")[0] in AUTH_PATHS:
            record.args = (peer, method, target.partition("?")[0], version, status)
    return True


def register_auth_routes(app: FastAPI) -> None:
    logging.getLogger("uvicorn.access").addFilter(redact_auth_query)

    @app.get("/v1/auth/config", tags=["auth"])
    async def auth_config(request: Request) -> dict[str, Any]:
        settings = app.state.settings
        sessions = bool(settings.admin_console_enabled or settings.oidc_issuer)
        return {
            "api_key": sessions and (settings.api_key_auth_enabled or settings.sandbox_budget_backend == "redis"),
            "jwt": sessions and settings.jwt_auth_enabled,
            "oidc": {
                "enabled": bool(settings.oidc_issuer),
                "provider_name": urlsplit(settings.oidc_issuer).hostname if settings.oidc_issuer else None,
                "login_url": "/v1/auth/login" if settings.oidc_issuer else None,
            },
        }

    @app.post("/v1/auth/session", tags=["auth"])
    async def login_key(request: Request, response: Response, body: SessionLogin) -> Any:
        require_sessions(request)
        check_origin(request)
        previous = await load_session(request)
        if previous:
            check_csrf(request, previous)
        try:
            error = await authenticate_credential(request, body.key.strip())
        except JwksUnavailableError as exc:
            raise HTTPException(503, detail="Sign-in keys are temporarily unavailable.") from exc
        if error is not None:
            return error
        return await create_session(request, response)

    @app.get("/v1/auth/session", tags=["auth"])
    async def get_session(request: Request, response: Response) -> Any:
        require_sessions(request)
        error = await bind_session(request)
        if error is not None:
            return error
        refresh_cookies(request, response)
        return session_info(request)

    @app.post("/v1/auth/logout", tags=["auth"])
    async def logout(request: Request, response: Response) -> dict[str, bool]:
        require_sessions(request)
        check_origin(request)
        session = await load_session(request)
        if session is not None:
            check_csrf(request, session)
            await redis_call(request, "delete", session_key(request, request.cookies[SESSION_COOKIE]))
        for name in (SESSION_COOKIE, CSRF_COOKIE, STATE_COOKIE):
            response.delete_cookie(name, **cookie_options(request))
        return {"signed_out": True}

    @app.get("/v1/auth/login", tags=["auth"], status_code=302, response_class=RedirectResponse)
    async def login_oidc(request: Request) -> RedirectResponse:
        data = await provider(request)
        options = cookie_options(request)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        await redis_call(request, "setex", session_key(request, state, "oidc"), 600,
                         json.dumps({"nonce": nonce, "verifier": verifier}))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        query = urlencode({
            "response_type": "code", "client_id": app.state.settings.oidc_client_id,
            "redirect_uri": app.state.settings.oidc_redirect_url, "scope": app.state.settings.oidc_scopes,
            "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256",
        })
        separator = "&" if "?" in data["authorization_endpoint"] else "?"
        response = RedirectResponse(data["authorization_endpoint"] + separator + query, 302)
        response.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, **options)
        return response

    @app.get("/v1/auth/callback", tags=["auth"], status_code=303, response_class=RedirectResponse)
    async def callback(request: Request, state: str = "", code: str = "", error: str = "") -> Response:
        try:
            return await complete_sign_in(request, state, code, error)
        except HTTPException as exc:
            # A browser landing here should see the console with a readable message, not JSON.
            if "text/html" not in request.headers.get("accept", ""):
                raise
            reason = "declined" if error else SIGNIN_ERRORS.get(exc.status_code, "unavailable")
            response = RedirectResponse(f"/console/?signin_error={reason}", 303)
            with suppress(HTTPException):
                response.delete_cookie(STATE_COOKIE, **cookie_options(request))
            response.headers["Referrer-Policy"] = "no-referrer"
            return response

    async def complete_sign_in(request: Request, state: str, code: str, error: str) -> Response:
        if not app.state.settings.oidc_issuer:
            raise HTTPException(404, detail="Company sign-in is not configured.")
        if not state or not same_token(state, request.cookies.get(STATE_COOKIE, "")):
            raise HTTPException(400, detail="Sign-in state did not match. Start sign-in again.")
        raw = await redis_call(request, "getdel", session_key(request, state, "oidc"))
        if not raw or not code or error:
            raise HTTPException(400, detail="Sign-in expired or was declined. Start sign-in again.")
        transaction = json.loads(raw)
        data = await provider(request)
        settings = app.state.settings
        form = {
            "grant_type": "authorization_code", "code": code, "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_url, "code_verifier": transaction["verifier"],
        }
        auth = None
        if settings.oidc_client_secret:
            methods = data.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
            if "client_secret_basic" in methods:
                auth = httpx.BasicAuth(
                    quote(settings.oidc_client_id, safe=""), quote(settings.oidc_client_secret, safe="")
                )
            elif "client_secret_post" in methods:
                form["client_secret"] = settings.oidc_client_secret
            else:
                raise HTTPException(503, detail="OIDC provider must support client_secret_basic or client_secret_post.")
        try:
            async with httpx.AsyncClient(timeout=min(settings.request_timeout_seconds, 10)) as client:
                result = await client.post(data["token_endpoint"], data=form, auth=auth)
                result.raise_for_status()
                token = result.json()["id_token"]
                if not isinstance(token, str):
                    raise ValueError("missing ID token")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(401, detail="Company sign-in could not be completed. Start sign-in again.") from exc
        await oidc_identity(request, token, transaction["nonce"])
        response = RedirectResponse("/console/", 303)
        await create_session(request, response)
        response.delete_cookie(STATE_COOKIE, **cookie_options(request))
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
