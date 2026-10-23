#!/usr/bin/env bash
# Compatibility alias; lifecycle policy lives in env.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/env.sh" start development --backend-only --reload --backend-port "${PORT:-8100}" "$@"
