import asyncio
from types import SimpleNamespace

import pytest
from app.settings import Settings
from app.tracing import build_tracer_provider, configure_tracing, trace_request
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind


def _settings(**overrides):
    base = {
        "runtime_backend": "ollama",
        "ollama_base_url": "http://ollama:11434",
        "vllm_base_url": "http://vllm:8000",
        "model_id": "default-model",
        "request_timeout_seconds": 5,
    }
    base.update(overrides)
    return Settings(**base)


class _FakeURL:
    path = "/v1/chat/completions"


class _FakeRequest:
    def __init__(self) -> None:
        self.method = "POST"
        self.url = _FakeURL()
        self.headers: dict[str, str] = {}
        self.scope = {"route": SimpleNamespace(path="/v1/chat/completions")}


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_configure_tracing_is_none_when_disabled():
    assert configure_tracing(_settings(otel_tracing_enabled=False)) is None


def test_settings_require_endpoint_when_tracing_enabled():
    with pytest.raises(ValueError, match="otel_exporter_otlp_endpoint"):
        _settings(otel_tracing_enabled=True)


def test_configure_tracing_returns_tracer_when_enabled():
    settings = _settings(otel_tracing_enabled=True, otel_exporter_otlp_endpoint="http://collector:4318")
    result = configure_tracing(settings, exporter=InMemorySpanExporter())
    assert result is not None
    tracer, provider = result
    assert tracer is not None
    assert provider is not None


def test_trace_request_exports_a_server_span():
    settings = _settings(
        otel_tracing_enabled=True,
        otel_exporter_otlp_endpoint="http://collector:4318",
    )
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(settings, exporter=exporter)
    tracer = provider.get_tracer("test")

    async def dispatch():
        return _FakeResponse(200)

    response = asyncio.run(trace_request(tracer, _FakeRequest(), dispatch))
    provider.force_flush()

    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "POST /v1/chat/completions"
    assert span.kind == SpanKind.SERVER
    assert span.attributes["http.response.status_code"] == 200
    assert span.attributes["http.route"] == "/v1/chat/completions"
    assert "url.path" not in span.attributes


def test_metrics_export_uses_bounded_routes_and_counts_errors():
    from app.tracing import configure_metrics, measure_request, signal_endpoint
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    assert signal_endpoint("http://collector:4318", "traces") == "http://collector:4318/v1/traces"
    assert signal_endpoint("http://collector:4318/v1/traces/", "metrics") == "http://collector:4318/v1/metrics"
    assert configure_metrics(_settings()) is None
    reader = InMemoryMetricReader()
    instruments = configure_metrics(
        _settings(otel_metrics_enabled=True, otel_exporter_otlp_endpoint="http://collector:4318"),
        reader,
    )

    async def failed():
        raise RuntimeError("secret error detail")

    request = _FakeRequest()
    request.scope = {"route": SimpleNamespace(path="/v1/workflow-runs/{run_id}/secrets/{name}/resolve")}
    with pytest.raises(RuntimeError):
        asyncio.run(measure_request(instruments, request, failed))
    data = reader.get_metrics_data()
    metrics = {
        metric.name: metric
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    count = metrics["agentworkflows_http_requests"].data.data_points[0]
    assert count.value == 1 and count.attributes["http.response.status_code"] == 500
    assert count.attributes["http.route"] == request.scope["route"].path
    assert "secret error detail" not in str(data)
    assert metrics["agentworkflows_http_request_duration_seconds"].data.data_points[0].count == 1
    assert metrics["agentworkflows_http_request_duration_seconds"].data.data_points[0].explicit_bounds[0] == 0.005
    instruments[2].shutdown()


def test_unknown_methods_and_unmatched_paths_do_not_create_metric_labels():
    from app.tracing import configure_metrics, measure_request
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    instruments = configure_metrics(
        _settings(otel_metrics_enabled=True, otel_exporter_otlp_endpoint="http://collector:4318"), reader
    )

    async def dispatch():
        return _FakeResponse(404)

    try:
        for index in range(10):
            request = _FakeRequest()
            request.method = f"SECRET-{index}"
            request.scope = {}
            request.url = SimpleNamespace(path=f"/private/{index}")
            asyncio.run(measure_request(instruments, request, dispatch))
        data = reader.get_metrics_data()
        metrics = [
            metric for resource in data.resource_metrics for scope in resource.scope_metrics for metric in scope.metrics
        ]
        count = next(metric for metric in metrics if metric.name == "agentworkflows_http_requests")
        assert len(count.data.data_points) == 1
        point = count.data.data_points[0]
        assert point.value == 10
        assert dict(point.attributes) == {
            "http.request.method": "_OTHER", "http.route": "/unmatched", "http.response.status_code": 404,
        }
        assert "SECRET" not in str(data) and "/private/" not in str(data)
    finally:
        instruments[2].shutdown()


@pytest.mark.parametrize("backend", ["ollama", "openai"])
def test_inbound_trace_propagates_gateway_span_to_runtime_without_credentials(monkeypatch, backend):
    from app.policy import ModelRoute, ModelRoutingPolicy
    from app.runtime_client import RuntimeClient
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    settings = _settings(otel_tracing_enabled=True, otel_exporter_otlp_endpoint="http://collector:4318")
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(settings, exporter)
    runtime = RuntimeClient(settings)
    runtime.policy = ModelRoutingPolicy((ModelRoute("default-model", backend, credential_env="TRACE_TEST_KEY"),))
    monkeypatch.setenv("TRACE_TEST_KEY", "provider-private-key")
    inbound = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    request = _FakeRequest()
    request.headers = {"traceparent": inbound, "tracestate": "vendor=opaque"}
    forwarded = {}

    async def dispatch():
        _, _, headers, _ = runtime._request_parts(
            {"messages": [], "model": "default-model"}, backend, "chat/completions",
            {"traceparent": inbound, "Authorization": "Bearer caller-private-key", "baggage": "secret=value"},
        )
        forwarded.update(headers)
        return _FakeResponse(200)

    try:
        asyncio.run(trace_request(provider.get_tracer("test"), request, dispatch))
        provider.force_flush()
        span, = exporter.get_finished_spans()
        assert span.parent.span_id == int("00f067aa0ba902b7", 16)
        from opentelemetry.trace import get_current_span

        propagated = get_current_span(TraceContextTextMapPropagator().extract(forwarded)).get_span_context()
        assert propagated.trace_id == span.context.trace_id and propagated.span_id == span.context.span_id
        assert forwarded["traceparent"] != inbound
        assert forwarded["tracestate"] == "vendor=opaque"
        if backend == "openai":
            assert "caller-private-key" not in str(forwarded) and "baggage" not in forwarded
        assert "private-key" not in str(span.attributes)
    finally:
        provider.shutdown()


def test_failed_trace_redacts_exception_text_and_unmatched_path():
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(_settings(), exporter)
    request = _FakeRequest()
    request.scope = {}
    request.url = SimpleNamespace(path="/secret-token")

    async def dispatch():
        raise RuntimeError("postgresql://user:secret-password@internal/db")

    try:
        with pytest.raises(RuntimeError):
            asyncio.run(trace_request(provider.get_tracer("test"), request, dispatch))
        provider.force_flush()
        span, = exporter.get_finished_spans()
        assert span.name == "POST /unmatched" and span.status.status_code.name == "ERROR"
        assert not span.events and span.status.description is None
        assert "secret" not in str(span.attributes)
    finally:
        provider.shutdown()
