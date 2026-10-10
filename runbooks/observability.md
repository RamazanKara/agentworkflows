# OTLP traces and metrics

The gateway exports OTLP over HTTP/protobuf using its existing OpenTelemetry dependency.
Enable either signal independently. In Compose set `OTEL_TRACING_ENABLED=true`,
`OTEL_METRICS_ENABLED=true`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318`
and optionally `OTEL_SERVICE_NAME=inference-gateway`. Use an endpoint reachable from the container.
The base URL is expanded to `/v1/traces` and `/v1/metrics`; a legacy full `/v1/traces`
endpoint is also accepted and its sibling metrics endpoint is derived.

Equivalent Helm values (under `inference-gateway` when using the umbrella chart):

```yaml
observability:
  tracing:
    enabled: true
    otlpEndpoint: http://otel-collector.monitoring.svc.cluster.local:4318
    serviceName: inference-gateway
  metrics:
    enabled: true
```

Allow collector egress in the chart's network policy. Configure collector authentication/TLS
and the standard OTLP exporter headers/environment through your deployment's secret mechanism.
The console's **Data & privacy** page and `GET /v1/team/telemetry` report configuration;
check collector logs and Grafana to confirm delivery.

Example collector pipelines, with an existing Tempo OTLP receiver and Prometheus scraping port 8889:

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
processors:
  batch: {}
exporters:
  otlp/tempo:
    endpoint: tempo.monitoring.svc.cluster.local:4317
    tls:
      insecure: true # Isolated local trial; configure TLS in production.
  prometheus:
    endpoint: 0.0.0.0:8889
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp/tempo]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [prometheus]
```

Import `deploy/observability/dashboards/otlp-dashboard.json` and select the Prometheus datasource
scraping that collector. The generated dashboard ConfigMaps include the same dashboard for
Grafana sidecar provisioning. Panels cover request rate, server errors, latency p95, workflow
states, monthly spend and limits. Team gauges use `max` across replicas because each replica
reads the same shared state; HTTP counters sum across replicas. Prometheus naming converts
OTLP `agentworkflows_http_requests` into `agentworkflows_http_requests_total` and replaces dots
in attribute names with underscores. If you apply a collector metric namespace prefix,
update the dashboard queries to match.

HTTP attributes use route patterns and method/status, which keeps raw run IDs and query strings
out of telemetry. Trace exception messages are suppressed to keep payloads and credentials out of traces. Existing
trace context propagation and workflow/team metadata remain available for correlation; set
appropriate collector access and retention. Duration measures the gateway response dispatch,
up to the point a streaming body begins. The Prometheus scrape endpoint keeps working alongside OTLP.
Export failures surface asynchronously, so monitor the collector.

Validate dashboard metric names/provisioning with `python scripts/dashboard-check.py --check`.
For live verification enable both signals, perform a run/cancel/retry, wait one metric export
interval, inspect the collector for both signals and verify the Grafana panels. Run this live
collector/Tempo/Prometheus check in your container or cluster environment.
See [Python exporters](https://opentelemetry.io/docs/languages/python/exporters/) for transport setup.


The rc.3 review keeps the existing metric names and dashboard queries. Unknown HTTP methods collapse
to `_OTHER`; unmatched paths collapse to `/unmatched`. Metric labels stay free of request IDs,
workflow run/step IDs, prompts, query strings and credentials. Sandbox Prometheus labels retain
the existing 2,000-value cap; team gauges are scoped to configured teams and removed on erasure.
Use `max` for replicated shared-state gauges and `sum(rate(...))` for per-process counters.

With tracing enabled, an inbound W3C parent creates a gateway SERVER span; the outgoing runtime
request uses that span as its parent. Cloud routes receive W3C trace context and their own provider
credentials; tenant headers and baggage stay inside the gateway. Gateway tracing covers the gateway
request path; configure Temporal history and SDK activity instrumentation separately, and validate
collector delivery in the deployment. Spans omit exception payloads and raw URL paths.

Audit-store failures produce ERROR logs and chain-persist error metrics, with driver
tracebacks (which may contain DSNs or SQL data) omitted. Worker activity errors retain status, machine reason
and gateway request ID while omitting response bodies; use the retained, access-controlled audit
trail for investigation. Native regression tests cover these redaction and propagation boundaries.
