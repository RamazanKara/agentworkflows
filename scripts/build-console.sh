#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/src/inference-gateway/console"
export RAYON_NUM_THREADS=2
npm ci --no-audit --no-fund
npm run build
