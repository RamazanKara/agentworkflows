#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/build-console.sh"
cd "$ROOT/src/inference-gateway/console"
npx playwright install chromium
npm test
