#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/build-console.sh"
cd "$ROOT/src/inference-gateway/console"
# PLAYWRIGHT_WITH_DEPS=1 also installs Chromium's system libraries (needs root or sudo; CI sets it).
npx playwright install ${PLAYWRIGHT_WITH_DEPS:+--with-deps} chromium
npm test
