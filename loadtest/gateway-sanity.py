#!/usr/bin/env python3
"""Native HTTP sanity load against an isolated gateway and the existing fake runtime."""

import asyncio
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx

ROOT = Path(__file__).resolve().parents[1]
COUNT = 500
CONCURRENCY = 10
KEY = "local-loadtest-only"
MODEL = "loadtest-model"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def wait_ready(client, url):
    for _ in range(120):
        try:
            if (await client.get(url)).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Server did not become ready: {url}")


async def main():
    runtime_port, gateway_port = port(), port()
    output = ROOT / "results/loadtest"
    output.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT / "src/inference-gateway"),
        "RUNTIME_BACKEND": "ollama", "OLLAMA_BASE_URL": f"http://127.0.0.1:{runtime_port}",
        "MODEL_ID": MODEL, "ALLOWED_MODELS": MODEL, "API_KEY_AUTH_ENABLED": "true",
        "API_KEY_SHA256S": hashlib.sha256(KEY.encode()).hexdigest(), "REQUEST_TIMEOUT_SECONDS": "5",
        "SANDBOX_BUDGET_BACKEND": "memory", "SANDBOX_BUDGET_ENABLED": "false",
        "AUDIT_LOG_ENABLED": "false", "RATE_LIMIT_ENABLED": "false", "ADMIN_CONSOLE_ENABLED": "false",
        "STORAGE_BACKEND": "redis", "TEMPORAL_ADDRESS": "", "MODEL_ROUTING_POLICY_PATH": "",
        "SANDBOX_POLICY_PATH": "", "API_KEY_RECORDS_PATH": "", "JWT_AUTH_ENABLED": "false",
        "OIDC_ISSUER": "", "METRICS_PORT": "0", "RESPONSE_CACHE_ENABLED": "false",
        "OTEL_TRACING_ENABLED": "false", "OTEL_METRICS_ENABLED": "false",
    }
    processes = []
    with (output / "gateway-sanity.log").open("w", encoding="utf-8") as log:
        try:
            for args in (
                [sys.executable, str(ROOT / "loadtest/mock-runtime.py"), "--port", str(runtime_port)],
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port",
                 str(gateway_port), "--no-access-log"],
            ):
                processes.append(subprocess.Popen(
                    args, cwd=ROOT, env=env, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                ))
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{gateway_port}", headers={"Authorization": f"Bearer {KEY}"},
                timeout=15, trust_env=False, limits=httpx.Limits(max_connections=CONCURRENCY),
            ) as client:
                await wait_ready(client, f"http://127.0.0.1:{runtime_port}/healthz")
                await wait_ready(client, "/healthz")
                message = [{"role": "user", "content": "Summarize the release readiness checks."}]
                cases = [
                    ("GET", "/healthz", None), ("GET", "/readyz", None),
                    ("GET", "/v1/models", None), ("GET", "/v1/usage", None),
                    ("GET", "/v1/sandbox/budget", None),
                    ("POST", "/v1/chat/completions", {"model": MODEL, "messages": message, "max_tokens": 32}),
                    ("POST", "/v1/responses", {
                        "model": MODEL, "input": message[0]["content"], "max_output_tokens": 32,
                    }),
                    ("POST", "/v1/messages", {"model": MODEL, "messages": message, "max_tokens": 32}),
                ]
                rows = []
                for method, path, body in cases:
                    for _ in range(10):
                        (await client.request(method, path, json=body)).raise_for_status()
                    latencies, statuses = [], {}

                    async def worker(method=method, path=path, body=body, latencies=latencies, statuses=statuses):
                        for _ in range(COUNT // CONCURRENCY):
                            started = perf_counter()
                            try:
                                response = await client.request(method, path, json=body)
                                status = str(response.status_code)
                            except httpx.HTTPError:
                                status = "transport_error"
                            latencies.append((perf_counter() - started) * 1000)
                            statuses[status] = statuses.get(status, 0) + 1

                    started = perf_counter()
                    await asyncio.gather(*(worker() for _ in range(CONCURRENCY)))
                    elapsed = perf_counter() - started
                    latencies.sort()
                    rows.append({
                        "method": method, "path": path, "requests": COUNT, "concurrency": CONCURRENCY,
                        "rps": round(COUNT / elapsed, 2), "statuses": statuses,
                        **{f"p{p}_ms": round(latencies[(COUNT * p + 99) // 100 - 1], 2) for p in (50, 95, 99)},
                    })
                    print(json.dumps(rows[-1]), flush=True)
                report = {
                    "date": datetime.now(UTC).isoformat(), "platform": platform.platform(),
                    "python": platform.python_version(), "cpu_count": os.cpu_count(),
                    "runtime": "local fake; no provider, Redis, Temporal or TLS", "results": rows,
                }
                (output / "gateway-sanity.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                if any(set(row["statuses"]) != {"200"} for row in rows):
                    raise RuntimeError("Load test returned unsuccessful responses; see gateway-sanity.json")
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                process.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(main())
