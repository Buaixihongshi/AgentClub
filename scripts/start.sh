#!/usr/bin/env bash
# Compatibility alias; lifecycle policy lives in env.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/env.sh" start local --open --backend-port "${PORT:-8000}" --frontend-port "${FRONTEND_PORT:-5173}" "$@"
