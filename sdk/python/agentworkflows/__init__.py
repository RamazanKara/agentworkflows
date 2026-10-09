"""First-party Python client for the AgentWorkflows inference gateway.

A thin wrapper that sets the platform headers
(``X-Sandbox-ID``, bearer auth) and covers the gateway's API: OpenAI-compatible chat,
completions, embeddings, moderations, Files/Batch and Responses, the Anthropic Messages
endpoint, agent-action receipts, and the sandbox usage/budget reads.

Retries are bounded, use exponential backoff, and honor the gateway's ``Retry-After`` up
to ``retry_after_cap`` seconds; a longer advertised delay fails fast with
:class:`GatewayRetryAfterError`, because retrying sooner cannot succeed. Calls that create
server-side state (file uploads, batches, stored responses, receipts) are retried only when
the gateway provably did not process the request, so a retry never duplicates a resource.

For the full OpenAI or Anthropic parameter surface, typed models, or async I/O, point the
official ``openai`` / ``anthropic`` SDKs at the gateway instead (see docs/client-examples.md).

    from agentworkflows import GatewayClient

    with GatewayClient("http://127.0.0.1:8080", api_key="...") as gw:
        reply = gw.chat([{"role": "user", "content": "hello"}])
        for chunk in gw.chat_stream([{"role": "user", "content": "stream please"}]):
            print(chunk, end="")
        print(gw.usage())
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping
from importlib import metadata
from types import TracebackType
from typing import Any, Unpack
from urllib.parse import quote
from uuid import UUID, uuid4

from temporalio import workflow as _workflow

from agentworkflows.types import (
    AuditFilters,
    AuditPage,
    AuditVerification,
    CaptureMode,
    CreatedKey,
    KeyList,
    KeyOptions,
    KeyUpdate,
    ManagedKey,
    RunFilters,
    RunPage,
    TeamSettings,
    TeamSettingValue,
    TeamSpend,
    TeamSSO,
)

with _workflow.unsafe.imports_passed_through():
    import httpx

__all__ = [
    "GatewayClient",
    "GatewayError",
    "GatewayRetryAfterError",
    "GatewayStreamError",
    "__version__",
]

try:
    __version__ = metadata.version("agentworkflows")
except metadata.PackageNotFoundError:  # running from a source checkout
    __version__ = "0+unknown"

# Retryable upstream statuses: 429 (rate limited / budget) and 5xx (transient server/runtime).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Statuses the gateway returns *before* doing any work (rate limit, load shed, backend
# unavailable). Only these, plus connection failures, are safe to retry for a call that
# creates server-side state.
_REJECTED_BEFORE_PROCESSING = frozenset({429, 503})
_NOT_SENT_ERRORS: tuple[type[httpx.HTTPError], ...] = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _parse_retry_after(value: str | None) -> int | None:
    """Parse a ``Retry-After`` header as non-negative integer seconds.

    The gateway only emits the delta-seconds form; malformed, negative, or absent
    values return ``None`` so the caller falls back to plain exponential backoff.
    """
    if value is None:
        return None
    try:
        seconds = int(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class GatewayError(httpx.HTTPStatusError):
    """An error response from the gateway, with its structured detail unpacked.

    Subclasses :class:`httpx.HTTPStatusError`, so existing ``except`` clauses keep working.
    ``reason`` is the machine-readable rejection (``model_not_allowed``,
    ``sandbox_token_budget_exceeded``, ...) and ``request_id`` is the id to quote when
    looking the call up in the audit trail.
    """

    def __init__(self, message: str, *, request: httpx.Request, response: httpx.Response) -> None:
        super().__init__(message, request=request, response=response)
        self.status_code = response.status_code
        self.detail = _error_detail(response)
        self.reason: str | None = self.detail.get("reason") if isinstance(self.detail.get("reason"), str) else None
        request_id = self.detail.get("request_id") or response.headers.get("X-Request-ID")
        self.request_id: str | None = str(request_id) if request_id else None


class GatewayRetryAfterError(GatewayError):
    """Raised without further retries when ``Retry-After`` exceeds ``retry_after_cap``.

    Retrying sooner than the advertised delay cannot succeed (typically an exhausted
    sandbox budget window); ``retry_after`` tells the caller when to come back.
    """

    def __init__(self, message: str, *, request: httpx.Request, response: httpx.Response, retry_after: int) -> None:
        super().__init__(message, request=request, response=response)
        self.retry_after = retry_after


class GatewayStreamError(RuntimeError):
    """Raised when the gateway emits a terminal error event mid-stream.

    The gateway signals an upstream failure after headers are sent with a final
    ``data: {"error": {...}}`` SSE event; surfacing it distinguishes a truncated
    stream from a completed one.
    """

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(str(error.get("message") or "gateway stream failed"))
        self.error = error


def _error_detail(response: httpx.Response) -> dict[str, Any]:
    """Return the gateway's error detail (``detail`` or OpenAI-style ``error``) as a dict."""
    try:
        body = response.json()
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    for key in ("detail", "error"):
        value = body.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return {"message": value}
        if key == "detail" and isinstance(value, list):
            fields = [".".join(str(part) for part in item.get("loc", [])) for item in value if isinstance(item, dict)]
            return {
                "reason": "invalid_request",
                "message": f"Invalid request fields: {', '.join(fields)}. "
                "Correct these fields using the command's --help or the API reference and retry.",
            }
    return {}


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = _error_detail(response)
    message = detail.get("message") or response.reason_phrase or "gateway request failed"
    reason = f" ({detail['reason']})" if isinstance(detail.get("reason"), str) else ""
    raise GatewayError(
        f"{response.status_code} {message}{reason}",
        request=response.request,
        response=response,
    )


class GatewayClient:
    """Client for the gateway's OpenAI- and Anthropic-compatible API with retry and streaming.

    ``sandbox_id`` is sent as ``X-Sandbox-ID`` when set. Leave it unset for a credential
    that is bound to a sandbox (an API-key record or a JWT tenant claim): the gateway then
    uses the bound sandbox, and naming a different one is rejected.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        sandbox_id: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
        retry_after_cap: float = 30.0,
        *,
        default_headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        verify: bool | str = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.sandbox_id = sandbox_id
        self.max_retries = max(0, max_retries)
        self.retry_backoff = retry_backoff
        self.retry_after_cap = retry_after_cap
        headers = dict(default_headers or {})
        if sandbox_id:
            headers["X-Sandbox-ID"] = sandbox_id
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        client_kwargs: dict[str, Any] = {"base_url": self.base_url, "headers": headers, "timeout": timeout}
        if transport is not None:
            client_kwargs["transport"] = transport
        else:
            client_kwargs["verify"] = verify
        self._client = httpx.Client(**client_kwargs)

    def __enter__(self) -> GatewayClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def _sleep(self, duration: float) -> None:
        # Overridable/patchable for tests.
        time.sleep(duration)

    def _retry_delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        """Return the delay before the next attempt.

        Exponential backoff (``retry_backoff * 2**attempt``), raised to the server's
        ``Retry-After`` when present. Headers beyond ``retry_after_cap`` never reach
        this method; :meth:`_request` fails fast with :class:`GatewayRetryAfterError`.
        """
        delay = self.retry_backoff * (2**attempt)
        retry_after = _parse_retry_after(response.headers.get("Retry-After")) if response is not None else None
        if retry_after is not None:
            delay = max(delay, min(float(retry_after), self.retry_after_cap))
        return delay

    def _request(self, method: str, path: str, *, creates_state: bool = False, **kwargs: Any) -> httpx.Response:
        """Issue a request, retrying transient failures with exponential backoff.

        Retryable responses (429/5xx) that carry a ``Retry-After`` header wait for the
        longer of the backoff and the advertised delay. A ``Retry-After`` strictly above
        ``retry_after_cap`` raises :class:`GatewayRetryAfterError` without any sleep.

        ``creates_state`` marks a call whose repetition would duplicate a resource (an
        upload, a batch, a stored response, a receipt). It is retried only on a connection
        failure or a 429/503, where the gateway provably did not act on the request.
        """
        for attempt in range(self.max_retries + 1):
            last_attempt = attempt >= self.max_retries
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                retryable = not creates_state or isinstance(exc, _NOT_SENT_ERRORS)
                if last_attempt or not retryable:
                    raise
                self._sleep(self._retry_delay(attempt))
                continue
            status = response.status_code
            retryable_status = status in (_REJECTED_BEFORE_PROCESSING if creates_state else _RETRYABLE_STATUS)
            if retryable_status:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                if retry_after is not None and retry_after > self.retry_after_cap:
                    raise GatewayRetryAfterError(
                        f"gateway advertised Retry-After {retry_after}s, beyond retry_after_cap"
                        f" ({self.retry_after_cap}s); not retrying",
                        request=response.request,
                        response=response,
                        retry_after=retry_after,
                    )
                if not last_attempt:
                    self._sleep(self._retry_delay(attempt, response))
                    continue
            _raise_for_status(response)
            return response
        raise RuntimeError("unreachable: the retry loop always returns or raises")  # pragma: no cover

    def _post(self, path: str, body: dict[str, Any], *, creates_state: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = self._request("POST", path, json=body, creates_state=creates_state).json()
        return result

    def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        result: dict[str, Any] = self._request("GET", path, **kwargs).json()
        return result

    # --- OpenAI-compatible inference ---

    def chat(self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Create a chat completion. Extra OpenAI params (tools, temperature, ...) pass through."""
        body: dict[str, Any] = {"messages": messages, **kwargs}
        if model is not None:
            body["model"] = model
        return self._post("/v1/chat/completions", body)

    def chat_stream(self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any) -> Iterator[str]:
        """Stream a chat completion, yielding assistant content deltas as they arrive.

        Parses the OpenAI-compatible SSE stream and yields the text of each
        ``choices[0].delta.content`` chunk; terminal ``[DONE]`` and non-text events are
        skipped. A terminal gateway ``error`` event raises :class:`GatewayStreamError`
        so a truncated stream is never mistaken for a completed one. An error status
        raises :class:`GatewayError` with the gateway's reason. The streaming path is not
        retried: once bytes flow, a retry would produce a second, different answer.
        """
        body: dict[str, Any] = {"messages": messages, "stream": True, **kwargs}
        if model is not None:
            body["model"] = model
        with self._client.stream("POST", "/v1/chat/completions", json=body) as response:
            if response.status_code >= 400:
                response.read()
                _raise_for_status(response)
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    parsed = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                error = parsed.get("error")
                if isinstance(error, dict):
                    raise GatewayStreamError(error)
                choices = parsed.get("choices")
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield content

    def completions(self, prompt: str | list[str], model: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Create a legacy text completion (``/v1/completions``)."""
        body: dict[str, Any] = {"prompt": prompt, **kwargs}
        if model is not None:
            body["model"] = model
        return self._post("/v1/completions", body)

    def embeddings(self, text: str | list[str], model: str | None = None) -> dict[str, Any]:
        """Create embeddings for a string or list of strings."""
        body: dict[str, Any] = {"input": text}
        if model is not None:
            body["model"] = model
        return self._post("/v1/embeddings", body)

    def moderations(self, text: str | list[str]) -> dict[str, Any]:
        """Classify input against the gateway content policy."""
        return self._post("/v1/moderations", {"input": text})

    def batch(self, requests: list[dict[str, Any]]) -> dict[str, Any]:
        """Run several chat completions in one synchronous call (``/v1/batch-inference``).

        Not the asynchronous OpenAI Batch API; for that see :meth:`create_batch`.
        """
        return self._post("/v1/batch-inference", {"requests": requests})

    # --- Anthropic Messages ---

    def messages(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        model: str | None = None,
        system: str | list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create an Anthropic-style message (``/v1/messages``) on the same governed path as chat."""
        body: dict[str, Any] = {"messages": messages, "max_tokens": max_tokens, **kwargs}
        if model is not None:
            body["model"] = model
        if system is not None:
            body["system"] = system
        return self._post("/v1/messages", body)

    # --- Asynchronous Files + Batch API (requires BATCH_API_ENABLED on the gateway) ---

    def upload_batch_file(self, content: bytes, filename: str = "batch.jsonl") -> dict[str, Any]:
        """Upload a JSONL input file (purpose='batch') and return the file object."""
        result: dict[str, Any] = self._request(
            "POST",
            "/v1/files",
            creates_state=True,
            files={"file": (filename, content, "application/jsonl")},
            data={"purpose": "batch"},
        ).json()
        return result

    def get_file(self, file_id: str) -> dict[str, Any]:
        """Retrieve a file object by id."""
        return self._get(f"/v1/files/{file_id}")

    def get_file_content(self, file_id: str) -> bytes:
        """Download raw file content (e.g. a batch output or error file)."""
        return self._request("GET", f"/v1/files/{file_id}/content").content

    def list_files(self) -> dict[str, Any]:
        """List uploaded files for this sandbox."""
        return self._get("/v1/files")

    def delete_file(self, file_id: str) -> dict[str, Any]:
        """Delete a file by id."""
        result: dict[str, Any] = self._request("DELETE", f"/v1/files/{file_id}").json()
        return result

    def create_batch(
        self,
        input_file_id: str,
        endpoint: str = "/v1/chat/completions",
        completion_window: str = "24h",
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create an asynchronous batch from an uploaded input file; returns the batch object."""
        body: dict[str, Any] = {
            "input_file_id": input_file_id,
            "endpoint": endpoint,
            "completion_window": completion_window,
        }
        if metadata is not None:
            body["metadata"] = metadata
        return self._post("/v1/batches", body, creates_state=True)

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        """Retrieve a batch object by id (poll its status)."""
        return self._get(f"/v1/batches/{batch_id}")

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        """Request cancellation of an in-progress batch."""
        return self._post(f"/v1/batches/{batch_id}/cancel", {})

    def list_batches(self, limit: int = 20, after: str | None = None) -> dict[str, Any]:
        """List batches for this sandbox, most recent first."""
        params: dict[str, Any] = {"limit": limit}
        if after is not None:
            params["after"] = after
        return self._get("/v1/batches", params=params)

    # --- OpenAI Responses API (stateful with store / previous_response_id when the gateway enables it) ---

    def create_response(
        self,
        input: str | list[dict[str, Any]],
        model: str | None = None,
        store: bool | None = None,
        previous_response_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Create a Responses API response.

        Set ``store=True`` (and ``previous_response_id`` to chain) for server-side
        conversation state, which needs ``RESPONSES_STORE_ENABLED`` on the gateway and,
        with authentication on, a credential bound to a sandbox.
        """
        body: dict[str, Any] = {"input": input, **kwargs}
        if model is not None:
            body["model"] = model
        if store is not None:
            body["store"] = store
        if previous_response_id is not None:
            body["previous_response_id"] = previous_response_id
        return self._post("/v1/responses", body, creates_state=bool(store))

    def get_response(self, response_id: str) -> dict[str, Any]:
        """Retrieve a stored response by id."""
        return self._get(f"/v1/responses/{response_id}")

    def delete_response(self, response_id: str) -> dict[str, Any]:
        """Delete a stored response by id."""
        result: dict[str, Any] = self._request("DELETE", f"/v1/responses/{response_id}").json()
        return result

    def response_input_items(self, response_id: str) -> dict[str, Any]:
        """List the input items of a stored response."""
        return self._get(f"/v1/responses/{response_id}/input_items")

    # --- Agent-action receipts (requires AGENT_RECEIPTS_ENABLED on the gateway) ---

    def record_receipt(self, action_type: str, decision: str, **fields: Any) -> dict[str, Any]:
        """Record an agent action on the audit hash chain.

        `action_type` is one of `egress_denied`, `egress_allowed`, `tool_exec`,
        `file_write`, `credential_request`, or `workspace_lifecycle`; `decision` is
        `allowed` or `denied`.
        The receipt is evidence that some other control acted; submitting one permits
        nothing. ``fields`` carries the optional receipt attributes (``target``, ``tool``,
        ``detail``, ...) documented in the gateway's OpenAPI contract.
        """
        body: dict[str, Any] = {"action_type": action_type, "decision": decision, **fields}
        return self._post("/v1/receipts", body, creates_state=True)

    # --- Discovery, accounting, and health ---

    def models(self) -> dict[str, Any]:
        """List the models this caller may use."""
        return self._get("/v1/models")

    def usage(self) -> dict[str, Any]:
        """Return this sandbox's usage and estimated cost."""
        return self._get("/v1/usage")

    def export_usage(self) -> str:
        """Export current UTC month usage as CSV, scoped to this credential's team/project."""
        return self._request("GET", "/v1/usage/export", headers={"Accept": "text/csv"}).text

    def team(self) -> dict[str, Any]:
        """Discover the current credential's team, role, and projects."""
        return self._get("/v1/team")

    def list_keys(self) -> KeyList:
        """List the team's managed keys, including revoked and expired keys (admin only)."""
        return self._request("GET", "/v1/team/keys").json()

    def create_key(self, name: str, **options: Unpack[KeyOptions]) -> CreatedKey:
        """Issue a managed key; its plaintext is returned only here. Role defaults to viewer.

        ``expires_at`` is an ISO-8601 timestamp with a timezone. Omit ``project`` for
        a team-wide key. Requires an admin credential with access to that project.
        """
        return self._request(
            "POST", "/v1/team/keys", json={"name": name, **options}, creates_state=True
        ).json()

    def update_key(self, key_id: str, **changes: Unpack[KeyUpdate]) -> ManagedKey:
        """Update only supplied key fields; None clears project or expiry (admin only)."""
        return self._request(
            "PATCH", f"/v1/team/keys/{quote(key_id, safe='')}", json=changes, creates_state=True
        ).json()

    def revoke_key(self, key_id: str) -> ManagedKey:
        """Revoke a managed key immediately; the current key cannot revoke itself."""
        return self._request("DELETE", f"/v1/team/keys/{quote(key_id, safe='')}", creates_state=True).json()

    def team_sso(self) -> TeamSSO:
        """Inspect this team's company sign-in policy (unrestricted admin only)."""
        return self._request("GET", "/v1/team/sso").json()

    def team_settings(self) -> TeamSettings:
        """Read effective settings, policy defaults and the revision (unrestricted admin only)."""
        return self._request("GET", "/v1/team/settings").json()

    def team_spend(self) -> TeamSpend:
        """Read monthly spend, limits and alerts (an unrestricted team credential is required)."""
        return self._request("GET", "/v1/team/spend").json()

    def set_spend_limits(
        self, *, soft_limit_usd: float | None, hard_limit_usd: float | None, revision: int | str
    ) -> TeamSettings:
        """Set monthly limits atomically; None disables a limit, zero is a zero-dollar limit."""
        return self.update_team_settings(
            {"soft_cost_limit_usd": soft_limit_usd, "cost_limit_usd": hard_limit_usd}, revision=revision
        )

    def set_content_capture(
        self, mode: CaptureMode, *, revision: int | str, workflow: str | None = None
    ) -> TeamSettings:
        """Set capture for future steps, for the team default or a named workflow."""
        field = f"workflows.{workflow}.capture_content" if workflow is not None else "capture_content"
        return self.update_team_settings({field: mode}, revision=revision)

    def update_team_settings(
        self, fields: Mapping[str, TeamSettingValue], *, revision: int | str
    ) -> TeamSettings:
        """Atomically override fields using a revision or quoted ETag in If-Match.

        Stale revisions raise GatewayError (409); invalid fields raise 422 with
        ``detail['fields']``. Reload and review conflicts before writing again.
        """
        return self._request(
            "PATCH", "/v1/team/settings", json={"fields": dict(fields)},
            headers={"If-Match": str(revision)}, creates_state=True,
        ).json()

    def reset_team_setting(self, field: str, *, revision: int | str) -> TeamSettings:
        """Reset one field to policy using its revision or ETag; stale writes raise 409."""
        return self._request(
            "DELETE", f"/v1/team/settings/{quote(field, safe='')}",
            headers={"If-Match": str(revision)}, creates_state=True,
        ).json()

    def audit(self, **filters: Unpack[AuditFilters]) -> AuditPage:
        """Read one newest-first audit page (unrestricted admin only).

        ``from_time`` and ``to`` are inclusive Unix seconds. Other filters match
        exactly; pass ``next_cursor`` as ``cursor`` for older events. ``limit``
        defaults to 50 on the gateway, with a maximum of 200.
        """
        params = {"from" if key == "from_time" else key: value for key, value in filters.items()}
        return self._request("GET", "/v1/team/audit", params=params).json()

    def verify_audit(self, *, from_time: float | None = None, to: float | None = None) -> AuditVerification:
        """Verify all retained team events in an inclusive Unix-time range.

        Verification does not accept actor/event/project/run filters. ``ok`` is
        None when disabled; boundaries describe a selected or retained prefix.
        """
        params = {key: value for key, value in {"from": from_time, "to": to}.items() if value is not None}
        return self._request("GET", "/v1/team/audit/verify", params=params).json()

    def export_audit(self, **filters: Unpack[AuditFilters]) -> Iterator[str]:
        """Yield original events as JSON Lines, following every matching cursor page.

        Each string ends with a newline. ``to`` defaults to the export start time
        and stays fixed during paging. Disabled views raise RuntimeError; failed
        reads propagate GatewayError/HTTPError, possibly after yielding some lines.
        Retention still applies; this is not a complete process log for the operator verifier.
        """
        filters.setdefault("to", time.time())
        while True:
            page = self.audit(**filters)
            if not page["enabled"]:
                raise RuntimeError(page.get("message") or "Audit view is off.")
            for entry in page["events"]:
                yield json.dumps(entry["event"]) + "\n"
            if page["next_cursor"] is None:
                return
            filters["cursor"] = page["next_cursor"]

    def triggers(self) -> dict[str, Any]:
        """List configured workflow triggers and their effective pause state."""
        return self._get("/v1/workflow-triggers")

    def pause_trigger(self, workflow: str, name: str, *, paused: bool = True) -> dict[str, Any]:
        response = self._request(
            "PATCH", f"/v1/workflow-triggers/{quote(workflow, safe='')}/{quote(name, safe='')}", json={"paused": paused}
        )
        return response.json()

    def start_run(
        self, workflow: str, input: Any, *, project: str | None = None, request_id: str | None = None
    ) -> dict[str, Any]:
        """Start once; reuse request_id after an ambiguous network failure."""
        return self._post(
            "/v1/workflow-runs",
            {"workflow": workflow, "input": input, "project": project, "request_id": request_id or str(uuid4())},
        )

    def runs(self, **filters: Unpack[RunFilters]) -> RunPage:
        """Read one run page; continue with next_cursor and unchanged project/filters.

        Limit bounds scanned records (1-100, default 20), so a filtered page can be
        empty with a continuation. Legacy offset paging remains available.
        """
        params = {key: value for key, value in {"offset": 0, **filters}.items() if value is not None}
        return self._request("GET", "/v1/workflow-runs", params=params).json()

    def export_runs(self, **filters: Unpack[RunFilters]) -> Iterator[str]:
        """Yield full run details, including retained step content, as JSON Lines.

        Follow cursors through empty pages. Retention and status changes continue
        during export; a read failure propagates and may leave partial output.
        """
        while True:
            page = self.runs(**filters)
            if "next_cursor" not in page:
                raise RuntimeError("Run export requires gateway v0.6.0 or newer; upgrade the gateway and retry.")
            for run in page["runs"]:
                yield json.dumps(self.run(run["run_id"])) + "\n"
            if page["next_cursor"] is None:
                return
            filters.pop("offset", None)
            filters["cursor"] = page["next_cursor"]

    def run(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/v1/workflow-runs/{UUID(run_id)!s}")

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        return self._post(f"/v1/workflow-runs/{UUID(run_id)!s}/cancel", {})

    def retry_run(self, run_id: str) -> dict[str, Any]:
        return self._post(f"/v1/workflow-runs/{UUID(run_id)!s}/retry", {})

    def approve_run(self, run_id: str, *, approved: bool = True) -> dict[str, Any]:
        return self._post(f"/v1/workflow-runs/{UUID(run_id)!s}/approve", {"approved": approved}, creates_state=True)

    def sandbox_budget(self) -> dict[str, Any]:
        """Return this sandbox's budget usage, limits, and window TTL."""
        return self._get("/v1/sandbox/budget")

    def ready(self) -> bool:
        """Return whether the gateway reports itself and its runtimes ready (``/readyz``)."""
        try:
            response = self._client.get("/readyz")
        except httpx.HTTPError:
            return False
        return response.status_code == 200
