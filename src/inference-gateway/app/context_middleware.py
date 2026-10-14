"""The gateway's outer request middleware: request context, authentication, and response headers.

A pure ASGI middleware rather than ``@app.middleware("http")``, for the same reason as
``ConcurrencyLimitMiddleware``. Starlette's ``BaseHTTPMiddleware`` runs the downstream app
in a task group and relays every response message through a memory stream, which cost
about half a millisecond per request, and it hands back the response before the body is
sent. The team request lease used to be released from a wrapper around that body
iterator, and a client that disconnected before the body started never ran the wrapper's
``finally``, so the lease leaked and the team could never be erased. Here
``await self.app(...)`` returns only after the last body chunk has been sent (and raises
if the request is cancelled), so the lease is released in one ``finally`` on every path.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.audit_view import audit_written
from app.budget import BudgetBackendError, backend_call
from app.console_auth import AUTH_PATHS
from app.jwt_auth import JwksUnavailableError
from app.metrics import RATE_LIMIT_FAIL_OPEN
from app.metrics import sandbox_label as _sandbox_label
from app.request_context import (
    GatewayJSONResponse,
    _api_key_from_request,
    _auth_required,
    _error_envelope,
    _jwks_unavailable_response,
    _rate_limit_backend_unavailable_response,
    _rate_limited_response,
    _request_id_from_header,
    _traceparent_from_header,
    authenticate_credential,
)
from app.sessions import SESSION_COOKIE, bind_session, refresh_cookies
from app.settings import Settings, validate_sandbox_id
from app.tracing import measure_request, trace_request
from app.workflow_api import bind_workflow
from app.workflow_credentials import bind_step_credential

LOGGER = logging.getLogger("uvicorn.error")
CONSOLE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; "
    "img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
CONSOLE_ASSETS = "/console/assets/"
# Paths a workflow-step identity may reach; everything else is refused before routing.
WORKFLOW_STEP_PATHS = frozenset({"/v1/chat/completions", "/v1/messages", "/v1/tools"})


@dataclass
class _Outcome:
    """What the tracing and OTel metric wrappers read back: the status that was sent."""

    status_code: int = 500


class RequestContextMiddleware:
    """Bind request identity, authenticate, throttle, and stamp the gateway response headers."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        try:
            self._bind_context(request)
        except ValueError as exc:
            # A request header failed validation. The message is a controlled, developer-authored
            # validation string describing the malformed header (e.g. the sandbox-id charset rule);
            # it carries no stack trace or server internals, so returning it as an actionable 400 is
            # correct API behavior, not information disclosure. Also logged for server-side triage.
            reason = str(exc)
            LOGGER.warning("rejected malformed request header: %s", reason)
            await JSONResponse(status_code=400, content={"detail": reason})(scope, receive, send)
            return
        outcome = _Outcome()

        async def dispatch() -> _Outcome:
            await self._dispatch(request, scope, receive, send, outcome)
            return outcome

        state = scope["app"].state
        tracer = state.tracer

        async def traced() -> Any:
            return await trace_request(tracer, request, dispatch) if tracer else await dispatch()

        if state.otel_metrics:
            await measure_request(state.otel_metrics, request, traced)
        else:
            await traced()

    def _bind_context(self, request: Request) -> None:
        state = request.state
        state.request_id = _request_id_from_header(request)
        state.sandbox_id = validate_sandbox_id(request.headers.get("x-sandbox-id", self.settings.default_sandbox_id))
        state.traceparent = _traceparent_from_header(request)
        bind_workflow(request)
        state.principal = None
        # True only when the sandbox id comes from a verified binding (an API-key record
        # with a sandbox, or a JWT tenant claim) rather than the client-asserted header.
        state.sandbox_bound = False
        # Per-key budget overrides (from a matched API-key record) folded into the
        # request's effective settings; empty for flat keys and unauthenticated paths.
        state.key_budget_updates = {}

    async def _dispatch(self, request: Request, scope: Scope, receive: Receive, send: Send, outcome: _Outcome) -> None:
        async def send_recording(message: Message) -> None:
            if message["type"] == "http.response.start":
                outcome.status_code = message["status"]
                # Answer only once this request's audit rows are stored (see audit_written).
                await audit_written(request)
            await send(message)

        path = request.url.path
        if path in AUTH_PATHS:
            await self._dispatch_sign_in(request, scope, receive, send_recording)
            return
        refused = await self._admit(request)
        if refused is not None:
            await refused(scope, receive, send_recording)
            return
        from app.team_data import enter_team_request, leave_team_request

        try:
            entered = await enter_team_request(request)
        except StarletteHTTPException as exc:
            await _error_response(exc)(scope, receive, send_recording)
            return
        try:
            await self.app(scope, _body_replay(request, receive), self._stamping(request, send_recording))
        finally:
            if entered:
                await leave_team_request(request)

    async def _dispatch_sign_in(self, request: Request, scope: Scope, receive: Receive, send: Send) -> None:
        if self.settings.rate_limit_enabled and request.url.path != "/v1/auth/config":
            # Unauthenticated callers cannot choose their throttle bucket through a team header.
            peer = request.client.host if request.client else "unknown"
            key = "auth:" + hashlib.sha256(peer.encode()).hexdigest()
            limiter = request.app.state.rate_limiter
            refused: Response | None
            try:
                allowed, retry_after = await backend_call(limiter, limiter.check, key)
            except BudgetBackendError:
                refused = _rate_limit_backend_unavailable_response(request)
            else:
                refused = None if allowed else _rate_limited_response(request, retry_after)
            if refused is not None:
                refused.headers["Cache-Control"] = "no-store"
                await refused(scope, receive, send)
                return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        await self.app(scope, _body_replay(request, receive), send_no_store)

    async def _admit(self, request: Request) -> Response | None:
        """Authenticate, authorize, and throttle the request; return a refusal or None to proceed."""
        from app.teams import authorize_team_request
        from app.workflow_triggers import bind_webhook

        settings = self.settings
        path = request.url.path
        try:
            webhook_credential = await bind_webhook(request)
            step_credential = False if webhook_credential else await bind_step_credential(request)
        except StarletteHTTPException as exc:
            return _error_response(exc)
        presented = _api_key_from_request(request, settings)
        if (
            not step_credential
            and not webhook_credential
            and (
                settings.api_key_auth_enabled
                or settings.jwt_auth_enabled
                or settings.oidc_issuer
                or request.cookies.get(SESSION_COOKIE)
                or (presented or "").startswith("aw_")
            )
            and _auth_required(path)
        ):
            try:
                if presented:
                    auth_error = await authenticate_credential(request)
                elif request.cookies.get(SESSION_COOKIE) and (settings.admin_console_enabled or settings.oidc_issuer):
                    auth_error = await bind_session(request)
                else:
                    auth_error = await authenticate_credential(request)
            except JwksUnavailableError:
                return _jwks_unavailable_response(request)
            except StarletteHTTPException as exc:
                return _error_response(exc)
            if auth_error is not None:
                return auth_error

        try:
            authorize_team_request(request)
        except StarletteHTTPException as exc:
            if isinstance(exc.detail, dict):
                exc.detail["request_id"] = request.state.request_id
            return _error_response(exc)

        # Short-window per-sandbox throttle (distinct from the cumulative budget): bounds
        # burst abuse. Checked after sandbox binding so the limit applies to the
        # authenticated tenant, not the spoofable header. A Redis-backed check runs on a
        # worker thread because the client is synchronous; a slow (not down) Redis must not
        # stall the whole event loop.
        if settings.rate_limit_enabled and _auth_required(path):
            limiter = request.app.state.rate_limiter
            try:
                allowed, retry_after = await backend_call(limiter, limiter.check, request.state.sandbox_id)
            except BudgetBackendError:
                # Deliberate availability-vs-enforcement tradeoff. Fail closed by default
                # (503, matching the budget tracker); when RATE_LIMIT_FAIL_OPEN is set,
                # admit the request instead so a Redis outage does not take down all
                # traffic - recorded via a warning log and a metric so the degraded
                # window is visible. Budgets are unaffected and stay fail-closed.
                if not settings.rate_limit_fail_open:
                    return _rate_limit_backend_unavailable_response(request)
                RATE_LIMIT_FAIL_OPEN.labels(_sandbox_label(request.state.sandbox_id)).inc()
                LOGGER.warning(
                    "rate limit backend unavailable; failing open (RATE_LIMIT_FAIL_OPEN=1) for sandbox %s request %s",
                    request.state.sandbox_id,
                    request.state.request_id,
                )
            else:
                if not allowed:
                    return _rate_limited_response(request, retry_after)

        # Bounded concurrency is enforced by ConcurrencyLimitMiddleware inside this one.
        if getattr(request.state, "workflow_run_id", None) and not _workflow_step_path(path):
            return JSONResponse(
                status_code=400,
                content={"detail": "Workflow steps support chat, messages, tools, and container agents."},
            )
        return None

    def _stamping(self, request: Request, send: Send) -> Send:
        """Wrap ``send`` to add the gateway's context headers to the response start."""

        async def send_with_context(message: Message) -> None:
            if message["type"] == "http.response.start":
                _stamp_headers(request, MutableHeaders(scope=message), message["status"])
            await send(message)

        return send_with_context


def _stamp_headers(request: Request, headers: MutableHeaders, status: int) -> None:
    state = request.state
    if getattr(state, "browser_session", None) is not None:
        # refresh_cookies speaks Starlette's Response API; render the cookies on a scratch
        # response and carry its Set-Cookie headers over.
        scratch = Response()
        refresh_cookies(request, scratch)
        for name, value in scratch.raw_headers:
            if name == b"set-cookie":
                headers.append("set-cookie", value.decode("latin-1"))
    path = request.url.path
    if path.startswith("/console"):
        headers["Content-Security-Policy"] = CONSOLE_CSP
        headers["X-Content-Type-Options"] = "nosniff"
    if path.startswith(CONSOLE_ASSETS) and status in (200, 304):
        # The console build content-hashes every file under assets/, so a cached copy can
        # never be stale and a repeat visit needs no request at all. index.html, which
        # names the current hashes, stays no-store.
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith(("/v1/", "/console")):
        headers["Cache-Control"] = "no-store"
    headers["X-Request-ID"] = state.request_id
    headers["X-Sandbox-ID"] = state.sandbox_id
    if state.traceparent:
        headers["traceparent"] = state.traceparent
    if getattr(state, "cache_status", None):
        headers["X-Cache"] = state.cache_status
    for header, value in getattr(state, "budget_headers", {}).items():
        headers[header] = value
    if getattr(state, "output_guardrail_action", None):
        headers["X-Output-Guardrail"] = state.output_guardrail_action
    if getattr(state, "prompt_guardrail_action", None):
        headers["X-Prompt-Guardrail"] = state.prompt_guardrail_action
    if getattr(state, "dropped_params", None):
        # Fields the parameter policy did not forward (app/params.py), so a client can
        # see why a runtime-specific option had no effect.
        headers["X-Dropped-Params"] = ",".join(state.dropped_params)


def _workflow_step_path(path: str) -> bool:
    return (
        path in WORKFLOW_STEP_PATHS
        or (path.startswith("/v1/tools/") and path.endswith("/call"))
        or (path.startswith("/v1/agents/") and path.endswith(("/start", "/finish")))
        or (path.startswith("/v1/workflow-runs/") and path.endswith("/resolve"))
    )


def _error_response(exc: StarletteHTTPException) -> JSONResponse:
    return GatewayJSONResponse(
        status_code=exc.status_code,
        content=_error_envelope(exc.status_code, exc.detail),
        headers=getattr(exc, "headers", None),
    )


def _body_replay(request: Request, receive: Receive) -> Receive:
    """Return a ``receive`` that replays the body when the middleware already read it.

    Webhook authentication hashes the raw body before routing. Once the middleware's
    ``Request`` has consumed the stream, the route would otherwise wait on ``receive``
    for a body that has already been delivered.
    """
    if not getattr(request, "_stream_consumed", False):
        return receive
    body: bytes = getattr(request, "_body", b"")
    replayed = False

    async def replay() -> Message:
        nonlocal replayed
        if not replayed:
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay
