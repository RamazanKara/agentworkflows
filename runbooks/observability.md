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
The console's **Data & privacy** page and `GET /v1/team/telemetry` report configuration,
not collector health. Check collector logs and Grafana to confirm delivery.

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
      insecure: true # Only for an isolated local trial; configure TLS in production.
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
in attribute names with underscores. Do not apply a collector metric namespace prefix unless
you also update the dashboard queries.

HTTP attributes use route patterns and method/status, never raw run IDs or query strings.
Trace exception messages are suppressed to avoid capturing payloads or credentials. Existing
trace context propagation and workflow/team metadata remain available for correlation; set
appropriate collector access and retention. Duration measures the gateway response dispatch;
streaming body lifetime is not included. The existing Prometheus scrape endpoint is unchanged.
Provider export failures are asynchronous and require collector monitoring.

Validate dashboard metric names/provisioning with `python scripts/dashboard-check.py --check`.
For live verification enable both signals, perform a run/cancel/retry, wait one metric export
interval, inspect the collector for both signals and verify the Grafana panels. This live
collector/Tempo/Prometheus check requires your container or cluster environment.
See [Python exporters](https://opentelemetry.io/docs/languages/python/exporters/) for transport setup.
