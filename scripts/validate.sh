#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/common.sh"
cd "$ROOT"

require_cmd python3 "Python 3 is required for gateway tests and YAML checks."
require_cmd helm "Helm is required to lint and render local charts."

log "bootstrapping Python validation environment"
./scripts/bootstrap-python.sh

log "checking validation toolchain manifest"
src/inference-gateway/.venv/bin/python scripts/toolchain-doctor.py --profile validate --check

log "running inference gateway tests"
./scripts/test-gateway.sh

log "running RAG service tests"
./scripts/test-rag.sh

log "checking the web console in headless Chromium"
./scripts/test-console.sh

log "fuzzing caller-controlled security parsers"
PYTHONPATH=src/inference-gateway src/inference-gateway/.venv/bin/python scripts/fuzz-security.py --iterations 2000

log "running Python lint, format, and type checks"
./scripts/quality.sh

./scripts/static-checks.sh

log "testing repository tooling"
make test-scripts

log "validation completed"
