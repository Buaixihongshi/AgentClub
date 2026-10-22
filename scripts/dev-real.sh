#!/usr/bin/env bash
# Compatibility alias; lifecycle policy lives in env.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/env.sh" start development --real-cli --providers "${PROVIDERS:-codex}" --backend-port "${PORT:-8100}" --frontend-port "${FRONTEND_PORT:-5174}" "$@"
