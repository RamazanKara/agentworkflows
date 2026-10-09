"""Optional OpenTelemetry tracing with OTLP/HTTP span export.

Tracing is opt-in. When ``OTEL_TRACING_ENABLED`` is false (the default) ``configure_tracing``
returns ``None`` and the request path adds no tracing overhead. When enabled, a SERVER span
is created per request, linked to any inbound W3C ``traceparent``, and exported over OTLP/HTTP
to ``OTEL_EXPORTER_OTLP_ENDPOINT``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

    from app.settings import Settings


def build_tracer_provider(settings: Settings, exporter: Any | None = None) -> Any:
    """Build a TracerProvider for the service; the span exporter is overridable for tests."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=signal_endpoint(settings.otel_exporter_otlp_endpoint, "traces"))
    provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def configure_tracing(settings: Settings, exporter: Any | None = None) -> tuple[Any, Any] | None:
    """Return ``(tracer, provider)`` when tracing is enabled, otherwise ``None``."""
    if not settings.otel_tracing_enabled:
        return None
    provider = build_tracer_provider(settings, exporter)
    return provider.get_tracer(settings.otel_service_name), provider


async def trace_request(
    tracer: Any,
    request: Request,
    dispatch: Callable[[], Awaitable[Response]],
) -> Response:
    """Run ``dispatch`` inside a SERVER span linked to the request's inbound trace context."""
    from opentelemetry.propagate import extract
    from opentelemetry.trace import SpanKind, StatusCode

    context = extract(dict(request.headers))
    with tracer.start_as_current_span(
        request.method,
        context=context,
        kind=SpanKind.SERVER,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        span.set_attribute("http.request.method", request.method)
        try:
            response = await dispatch()
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                span.set_status(StatusCode.ERROR)
            return response
        except Exception:
            span.set_status(StatusCode.ERROR)
            raise
        finally:
            route = request_route(request)
            span.update_name(f"{request.method} {route}")
            span.set_attribute("http.route", route)


def request_route(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", "/unmatched")


def signal_endpoint(endpoint: str, signal: str) -> str:
    base = endpoint.rstrip("/")
    for suffix in ("/v1/traces", "/v1/metrics"):
        base = base.removesuffix(suffix)
    return f"{base}/v1/{signal}"


def configure_metrics(settings: Settings, reader: Any = None) -> tuple[Any, Any, Any] | None:
    if not settings.otel_metrics_enabled:
        return None
    from opentelemetry.metrics import Observation
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import Resource

    from app.metrics import TEAM_COST_LIMIT, TEAM_SPEND, WORKFLOW_RUNS, WORKFLOW_STATUS_REFRESH

    if reader is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=signal_endpoint(settings.otel_exporter_otlp_endpoint, "metrics"),
            )
        )
    provider = MeterProvider(
        resource=Resource.create({"service.name": settings.otel_service_name}), metric_readers=[reader]
    )
    meter = provider.get_meter(settings.otel_service_name)
    for name, gauge in (
        ("agentworkflows_workflow_runs", WORKFLOW_RUNS),
        ("agentworkflows_team_spend_usd", TEAM_SPEND),
        ("agentworkflows_team_cost_limit_usd", TEAM_COST_LIMIT),
        ("agentworkflows_workflow_status_refresh_seconds", WORKFLOW_STATUS_REFRESH),
    ):

        def observe(options: Any, gauge: Any = gauge) -> list[Any]:
            return [Observation(sample.value, sample.labels) for metric in gauge.collect() for sample in metric.samples]

        meter.create_observable_gauge(name, callbacks=[observe])
    return (
        meter.create_counter("agentworkflows_http_requests"),
        meter.create_histogram(
            "agentworkflows_http_request_duration_seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 120),
        ),
        provider,
    )


async def measure_request(
    instruments: tuple[Any, Any, Any], request: Request, dispatch: Callable[[], Awaitable[Response]]
) -> Response:
    start = perf_counter()
    code = 500
    try:
        response = await dispatch()
        code = response.status_code
        return response
    finally:
        attributes = {
            "http.request.method": request.method,
            "http.route": request_route(request),
            "http.response.status_code": code,
        }
        instruments[0].add(1, attributes)
        instruments[1].record(perf_counter() - start, attributes)
