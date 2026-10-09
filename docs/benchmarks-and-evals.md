# Benchmarks And Evals

The repository includes lightweight eval and load-test paths for release hygiene. They are not a substitute for customer workload benchmarks.

## Evals

Eval suites live under `platform/evals/`.

```bash
make eval-local
make eval
SUITE=platform/evals/coding-agent-suite.yaml make eval
```

`make eval-local` uses an ephemeral mock runtime for current release-gate evidence without a live cluster. `make eval` targets a live gateway. The default smoke suite proves that the selected gateway/model path can answer basic platform prompts within configured latency limits. The coding-agent suite exercises agent-oriented prompts. Customer teams should add cases for their languages, tools, retrieval corpus, safety policy, and expected failure modes.

## Load Tests

For an isolated gateway-path check without a cluster or real model runtime:

```bash
make loadtest-local
```

For a live gateway:

```bash
GATEWAY_URL=http://127.0.0.1:8080 make loadtest
```

The release gates check request count, error rate, p95 latency, and p99 latency. Tune thresholds in `platform/slo/release-gates.yaml` only when the target environment and workload justify the change.

## Reference Serving Benchmark

### rc.3 native gateway baseline (2026-10-09)

The recorded report is `loadtest/baselines/rc3-windows.json`: Windows 11 build 26200,
Python 3.12.14, 16 logical CPUs, one Uvicorn process, 10 concurrent clients,
500 requests per route after 10 warmups. An independent second run passed the regression
checker. Both runs returned HTTP 200 for all 4,000 measured requests.

| Endpoint | Baseline requests/s | Baseline p95 ms | Second run requests/s | Second run p95 ms |
| --- | ---: | ---: | ---: | ---: |
| `GET /healthz` | 235.08 | 54.96 | 403.86 | 18.00 |
| `GET /readyz` | 112.71 | 115.65 | 201.12 | 63.88 |
| `GET /v1/models` | 189.10 | 84.88 | 335.22 | 20.84 |
| `GET /v1/usage` | 169.66 | 118.36 | 337.69 | 68.31 |
| `GET /v1/sandbox/budget` | 175.09 | 70.89 | 342.37 | 76.64 |
| `POST /v1/chat/completions` | 77.12 | 170.62 | 134.52 | 93.55 |
| `POST /v1/responses` | 87.32 | 161.02 | 106.59 | 137.82 |
| `POST /v1/messages` | 100.70 | 140.24 | 116.38 | 103.13 |

```sh
python loadtest/gateway-sanity.py
python loadtest/check-regression.py loadtest/baselines/rc3-windows.json results/loadtest/gateway-sanity.json
```

The checker fails on any failed/missing request, p95 above 125% of baseline or throughput
below 80%. It also rejects invalid/nonfinite measurements, changed workloads and mismatched
platform/Python/CPU-count/runtime metadata. Establish a separate baseline on other hardware;
keep an accepted baseline unchanged when evaluating a candidate. The JSON records p50/p99 too.

This is a shared-host smoke measurement; the spread between these two runs shows substantial
host noise. It does not establish a performance change from the earlier candidate or a capacity
SLO. Authentication is enabled; audit output, caching, rate limits, budget enforcement and OTLP
export are disabled, with memory accounting and a constant fake model response. Redis,
PostgreSQL, Temporal, streaming, TLS and real providers are excluded. The harness records
transport failures as errors instead of losing the report. Run the Compose workflow harness
and Linux load checks below for deployment evidence.

### 1.0 candidate native gateway sanity (2026-10-09)

Run `python loadtest/gateway-sanity.py` from an environment containing the gateway's
runtime dependencies. It starts and stops its own loopback gateway and the existing
fake runtime, then writes `results/loadtest/gateway-sanity.json` and a process log.
This run used Windows 11 (build 26200), Python 3.12.14 and 16 logical CPUs, one Uvicorn
process, 10 concurrent clients and 500 measured requests per endpoint after 10 warmups.
All **4,000 measured requests returned 200**. Percentiles use nearest-rank latency.

| Endpoint | Requests/s | p50 ms | p95 ms | p99 ms |
| --- | ---: | ---: | ---: | ---: |
| `GET /healthz` | 485.71 | 8.76 | 19.92 | 490.71 |
| `GET /readyz` | 192.59 | 46.03 | 75.73 | 141.70 |
| `GET /v1/models` | 386.17 | 10.56 | 47.52 | 364.13 |
| `GET /v1/usage` | 507.38 | 15.32 | 43.51 | 91.67 |
| `GET /v1/sandbox/budget` | 605.01 | 12.90 | 33.58 | 62.68 |
| `POST /v1/chat/completions` | 149.87 | 65.26 | 90.21 | 132.73 |
| `POST /v1/responses` | 139.54 | 67.10 | 102.35 | 141.77 |
| `POST /v1/messages` | 136.44 | 69.11 | 113.12 | 165.14 |

API-key authentication was enabled; audit output, caching, rate limits and budget
enforcement were disabled, with memory accounting. The fake returns a short constant
answer; requests allow at most 32 output tokens. Client, gateway and fake share the
host, which also ran validation work. These numbers establish a local smoke baseline,
not capacity or a production SLO. They exclude Redis/PostgreSQL, browser sessions,
Temporal run start/list/approve, signed triggers, streaming, embeddings, Files/Batch,
TLS and real provider latency. Run `make workflow-loadtest` against Compose and
`make loadtest-local`/a deployment-specific load test in WSL before release acceptance.

### Model serving reference

A real, reproducible serving measurement for the default local model. This is a hardware reference, not a guarantee; re-run it on your own machine.

`qwen2.5:0.5b` (494M parameters, Q4_K_M; Ollama registry model-layer digest `sha256:c5396e06af294bd101b30dce59131a76d2b773e76950acc870eda801d3ab0515`) on an **AMD Ryzen 7 5800X3D** (CPU only, no GPU), 20 runs after warmup, `num_predict=100`, `temperature=0`. Results:

| metric | p50 | p95 | mean |
| --- | --- | --- | --- |
| end-to-end latency (s) | 0.53 | 0.56 | 0.49 |
| generation throughput (tokens/s) | 55.4 | 58.1 | 55.2 |

Reproduce on your own hardware:

```bash
make benchmark-local
# or tune: MODEL=qwen2.5:0.5b RUNS=20 NUM_PREDICT=100 scripts/benchmark-ollama.sh
```

`scripts/benchmark-ollama.sh` reuses an Ollama at `OLLAMA_URL` if reachable, otherwise starts a throwaway Ollama container, pulls the model, warms up, and reports the latency and throughput distribution. GPU/vLLM throughput is materially higher and concurrency-dependent; size the GPU tier with the vLLM/GPU Grafana dashboard and `runbooks/gpu-capacity.md` before production.


## What These Tests Prove

- Gateway admission, auth, trace headers, and runtime forwarding can handle repeat traffic.
- Release reports have machine-checkable metrics.
- Strict gates can reject stale or sample evidence.

## What They Do Not Prove

- Production model quality for a customer's domain.
- Peak GPU throughput under real concurrency.
- Long-context behavior for a customer corpus.
- Full resilience under node, storage, ingress, or secret-backend failures.
