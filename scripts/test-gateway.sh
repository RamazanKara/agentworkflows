#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"

"$ROOT/scripts/build-console.sh"

ensure_service_venv "$ROOT/src/inference-gateway"
cd "$ROOT/src/inference-gateway"
PYTHONPATH="$PWD" .venv/bin/python -m pytest -q -s tests --ignore=tests/live

# Framework examples add optional SDK test dependencies beyond the gateway runtime.
.venv/bin/python -m pip install --quiet --require-hashes -r "$ROOT/requirements-sdk-test.lock"
PYTHONPATH="$ROOT/sdk/python" .venv/bin/python -m pytest -q -s "$ROOT/sdk/python/tests"
