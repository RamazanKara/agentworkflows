"""Runtime failover and shadow-routing helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastapi import HTTPException, Request

from app.governance import effective_settings, governed, reserve_budget, route_settings, workflow_route_permitted
from app.metrics import RUNTIME_FALLBACKS, SHADOW_REQUESTS
from app.request_context import _runtime_headers
from app.runtime_client import RuntimeClient
from app.settings import AdmissionPolicyError
from app.team_settings import forget_team_settings
from app.workflow_budget import (
    SETTLE,
    load_run_policy,
    model_charge,
    redis_call,
    reserve_run,
    run_key,
    settle_run_model,
)


def _schedule_shadow(client: RuntimeClient, shadow_route: Any, payload_dict: dict[str, Any], request: Request) -> None:
    """Fire a mirrored request to the shadow model, discarding its response and errors.

    Runs as a detached task so it never adds latency to or fails the caller's request;
    used to evaluate a candidate model on real traffic before promotion.
    """
    shadow_payload = dict(payload_dict)
    shadow_payload["model"] = shadow_route.model_id
    shadow_payload.pop("stream", None)
    headers = _runtime_headers(request)

    async def _run() -> None:
        try:
            shadow_request = Request({**request.scope, "state": dict(request.scope["state"])})
            shadow_request.state.selected_route = shadow_route
            shadow_request.state.cost_routes = [shadow_route]
            shadow_request.state.routing_role = "shadow"
            shadow_request.state.routing_attempts = []
            shadow_request.state.budget_settled = False
            shadow_request.state.cache_status = None
            settings = request.app.state.settings
            async with governed(shadow_request, settings, route=request.url.path, payload=shadow_payload) as call:
                effective = route_settings(
                    effective_settings(shadow_request, request.app.state.sandbox_policy_set, settings), shadow_route
                )
                effective.validate_admission(shadow_payload)
                await reserve_budget(shadow_request, effective, shadow_payload)
                call.runtime_response = await client.chat_completions(
                    shadow_payload, headers=headers, backend=shadow_route.backend
                )
            SHADOW_REQUESTS.labels(shadow_route.backend, "ok").inc()
        except Exception:
            SHADOW_REQUESTS.labels(shadow_route.backend, "error").inc()

    # Hold a strong reference until completion so the detached task is not GC'd mid-flight.
    tasks: set[asyncio.Task[None]] = request.app.state.background_tasks
    task = asyncio.ensure_future(_run())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _is_failover_worthy(exc: Exception) -> bool:
    """Return whether an upstream failure should trigger a fallback to the next route.

    Connection/transport errors and an open circuit always fail over; an HTTP status
    error fails over only for retryable server-side statuses (5xx/429), never a client
    error like 400/404 that the next runtime would also reject.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, httpx.HTTPError)


def _start_attempt(request: Request, candidate: Any, payload: dict[str, Any]) -> dict[str, Any]:
    request.state.selected_route = candidate
    payload["model"] = candidate.model_id
    attempts = getattr(request.state, "routing_attempts", None)
    if attempts is None:
        attempts = []
        request.state.routing_attempts = attempts
    attempt = {"provider": candidate.backend, "model": candidate.model_id}
    attempts.append(attempt)
    return attempt


async def _chat_with_fallback(
    client: RuntimeClient, chain: list[Any], payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    for index, candidate in enumerate(chain):
        attempt = _start_attempt(request, candidate, payload)
        workflow_call = bool(getattr(request.state, "workflow_run_id", None))
        reservation = None
        if workflow_call:
            if index:
                # The previous attempt may have run for minutes: re-read the team's settings
                # so a workflow policy tightened meanwhile governs the fallback.
                forget_team_settings(request)
            await load_run_policy(request)
            effective = route_settings(
                effective_settings(request, request.app.state.sandbox_policy_set, request.app.state.settings), candidate
            )
            if not workflow_route_permitted(request, effective, candidate):
                raise AdmissionPolicyError("workflow_model_denied", "The workflow no longer allows this route.")
            reservation = await reserve_run(request, *model_charge(effective, candidate, payload))
            attempt["reserved"] = request.state.workflow_charge
        try:
            options = {"retry": False} if workflow_call else {}
            result = await client.chat_completions(
                dict(payload), headers=_runtime_headers(request), backend=candidate.backend, **options
            )
            attempt["status"] = "served"
            await settle_run_model(request, reservation, candidate, result)
            if workflow_call:
                attempt["charged"] = request.state.workflow_charge
            return result
        except AdmissionPolicyError:
            # The provider adapter refused the call before sending it.
            await _release_run_reservation(request, reservation, attempt)
            raise
        except httpx.HTTPError as exc:
            attempt["status"] = "failed"
            if isinstance(exc, httpx.HTTPStatusError):
                attempt["status_code"] = exc.response.status_code
            if not provider_processed(exc):
                await _release_run_reservation(request, reservation, attempt)
            if _is_failover_worthy(exc) and index + 1 < len(chain):
                RUNTIME_FALLBACKS.labels(candidate.backend, chain[index + 1].backend).inc()
                continue
            _refuse_cloud_fallback(request, exc)
            raise
    raise RuntimeError("no runtime route available")


def provider_processed(exc: httpx.HTTPError) -> bool:
    """Whether a failed call may have consumed provider capacity, so its charge stands.

    Only failures that prove the provider did no work count as unprocessed: no connection
    (including an open circuit, a connect timeout, or no free pool connection) and an
    explicit refusal to take the request (429, 503). A read timeout, a dropped connection,
    or any other 5xx is ambiguous - the provider may have generated - so it keeps the
    conservative charge.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code not in {429, 503}
    return not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))


async def _release_run_reservation(
    request: Request, reservation: tuple[int, int] | None, attempt: dict[str, Any]
) -> None:
    """Return a workflow attempt's run-budget reservation for a call no provider processed.

    Without it, each failed attempt kept its worst-case reservation: a provider outage
    across activity retries and fallback routes could exhaust a run's budget with no
    real spend. A refund that cannot be written keeps the charge, which only over-counts.
    """
    if reservation is None:
        return
    try:
        await redis_call(request, "eval", SETTLE, 1, run_key(request), -reservation[0], -reservation[1])
    except HTTPException:
        logging.getLogger("uvicorn.error").warning("Run budget refund failed; the reservation stands.")
        return
    attempt["refunded"] = True
    request.state.workflow_charge = None


def _refuse_cloud_fallback(request: Request, exc: httpx.HTTPError) -> None:
    if _is_failover_worthy(exc) and getattr(request.state, "classification_blocked_routes", []):
        raise AdmissionPolicyError(
            "data_classification_denied", "local models are unavailable; confidential data cannot fall back to cloud"
        ) from exc


async def _open_stream_with_fallback(
    client: RuntimeClient,
    chain: list[Any],
    payload_dict: dict[str, Any],
    request: Request,
) -> tuple[Any, str, str, bytes | None]:
    """Open a chat stream, failing over to the next route on a pre-first-byte error.

    Returns the live stream generator, the backend and model id that served it, and the
    primed first chunk (``None`` for an empty stream). Once the first chunk is returned
    the response is committed; later failures are handled by the stream body itself.
    """
    last_exc: httpx.HTTPError | None = None
    for index, candidate in enumerate(chain):
        receipt = _start_attempt(request, candidate, payload_dict)
        attempt = dict(payload_dict)
        attempt["model"] = candidate.model_id
        candidate_stream = client.stream_chat_completions(
            attempt,
            headers=_runtime_headers(request),
            backend=candidate.backend,
        )
        try:
            first_chunk = await candidate_stream.__anext__()
        except StopAsyncIteration:
            receipt["status"] = "served"
            return candidate_stream, candidate.backend, candidate.model_id, None
        except httpx.HTTPError as exc:
            receipt["status"] = "failed"
            if isinstance(exc, httpx.HTTPStatusError):
                receipt["status_code"] = exc.response.status_code
            await candidate_stream.aclose()
            last_exc = exc
            if _is_failover_worthy(exc) and index + 1 < len(chain):
                RUNTIME_FALLBACKS.labels(candidate.backend, chain[index + 1].backend).inc()
                continue
            _refuse_cloud_fallback(request, exc)
            raise
        receipt["status"] = "served"
        return candidate_stream, candidate.backend, candidate.model_id, first_chunk
    raise last_exc or RuntimeError("no runtime route available")
