"""Opaque browser sessions; credentials and authorization stay on the gateway."""

import hashlib
import json
import secrets
from time import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response

from app.oidc_access import access_policy_digest
from app.request_context import _sandbox_binding_response, authenticate_credential
from app.settings import validate_sandbox_id
from app.workflow_budget import redis_call

SESSION_COOKIE = "aw_session"
CSRF_COOKIE = "aw_csrf"
IDLE_SECONDS = 12 * 60 * 60
MAX_SECONDS = 7 * 24 * 60 * 60

TOUCH = """
local raw = redis.call('GET', KEYS[1])
if not raw then return nil end
local session = cjson.decode(raw)
local remaining = math.floor(session.absolute_expires_at - tonumber(ARGV[1]))
if remaining <= 0 then
  redis.call('DEL', KEYS[1])
  return nil
end
redis.call('EXPIRE', KEYS[1], math.min(remaining, tonumber(ARGV[2])))
return raw
"""


def session_key(request: Request, token: str, kind: str = "session") -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()
    return f"{request.app.state.settings.sandbox_budget_key_prefix}:{kind}:{digest}"


def cookie_options(request: Request) -> dict[str, Any]:
    secure = request.app.state.settings.session_cookie_secure
    if not secure and request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(503, detail="SESSION_COOKIE_SECURE=false is only supported on localhost.")
    return {"secure": secure, "samesite": "lax", "path": "/"}


def same_token(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode(), right.encode())


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    redirect = urlsplit(request.app.state.settings.oidc_redirect_url)
    # TLS can terminate at the ingress while the gateway sees HTTP internally.
    scheme = "https" if request.app.state.settings.session_cookie_secure else request.url.scheme
    expected = f"{redirect.scheme}://{redirect.netloc}" if redirect.netloc else f"{scheme}://{request.url.netloc}"
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != expected):
        raise HTTPException(403, detail="Sign in from the gateway's own console.")


def check_csrf(request: Request, session: dict[str, Any]) -> None:
    if request.method != "GET" and (
        not same_token(request.headers.get("x-csrf-token", ""), session["csrf_token"])
        or not same_token(request.cookies.get(CSRF_COOKIE, ""), session["csrf_token"])
    ):
        raise HTTPException(403, detail={"reason": "csrf_invalid", "message": "Refresh your session and retry."})


async def load_session(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    raw = await redis_call(request, "eval", TOUCH, 1, session_key(request, token), time(), IDLE_SECONDS)
    return json.loads(raw) if raw else None


async def bind_session(request: Request) -> Response | None:
    session = await load_session(request)
    if session is None:
        raise HTTPException(401, detail="Your session expired. Sign in again.")
    check_csrf(request, session)
    settings = request.app.state.settings
    digest = session.get("oidc_policy_digest")
    if (
        (digest is not None and digest != access_policy_digest(settings, session["sandbox_id"]))
        or (
            settings.oidc_group_role_mappings and "oidc_policy_digest" not in session
            and not session.get("credential_digest")
        )
    ):
        await redis_call(request, "delete", session_key(request, request.cookies[SESSION_COOKIE]))
        raise HTTPException(401, detail="Company sign-in policy changed. Sign in again.")
    if session.get("credential_digest"):
        error = await authenticate_credential(request, "", digest=session["credential_digest"])
        if error is not None:
            return error
    else:
        request.state.principal = session["principal"]
        request.state.sandbox_bound = session["sandbox_bound"]
        request.state.sandbox_id = session["sandbox_id"]
    explicit = request.headers.get("x-sandbox-id")
    if (
        request.state.sandbox_bound and explicit is not None
        and validate_sandbox_id(explicit) != request.state.sandbox_id
    ):
        return _sandbox_binding_response(request, "sandbox_identity_mismatch")
    request.state.browser_session = session
    return None


def refresh_cookies(request: Request, response: Response, token: str | None = None) -> None:
    session = getattr(request.state, "browser_session", None)
    if session is None:
        return
    ttl = max(0, min(IDLE_SECONDS, int(session["absolute_expires_at"] - time())))
    options = cookie_options(request)
    response.set_cookie(SESSION_COOKIE, token or request.cookies[SESSION_COOKIE], max_age=ttl, httponly=True, **options)
    response.set_cookie(CSRF_COOKIE, session["csrf_token"], max_age=ttl, httponly=False, **options)


async def create_session(request: Request, response: Response) -> dict[str, Any]:
    cookie_options(request)
    token = secrets.token_urlsafe(32)
    session = {
        "principal": request.state.principal, "sandbox_id": request.state.sandbox_id,
        "sandbox_bound": request.state.sandbox_bound,
        "credential_digest": getattr(request.state, "credential_digest", None),
        "oidc_policy_digest": getattr(request.state, "oidc_policy_digest", None),
        "absolute_expires_at": min(time() + MAX_SECONDS, getattr(request.state, "credential_expires_at", float("inf"))),
        "csrf_token": secrets.token_urlsafe(32),
    }
    ttl = min(IDLE_SECONDS, int(session["absolute_expires_at"] - time()))
    if ttl <= 0:
        raise HTTPException(401, detail="Your credential expired. Sign in again.")
    await redis_call(request, "setex", session_key(request, token), ttl, json.dumps(session))
    old = request.cookies.get(SESSION_COOKIE)
    if old:
        await redis_call(request, "delete", session_key(request, old))
    request.state.browser_session = session
    refresh_cookies(request, response, token)
    return session_info(request)


def session_info(request: Request) -> dict[str, Any]:
    session = request.state.browser_session
    return {
        "principal": request.state.principal, "sandbox_id": request.state.sandbox_id,
        "csrf_token": session["csrf_token"], "absolute_expires_at": session["absolute_expires_at"],
    }
