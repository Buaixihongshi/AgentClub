#!/usr/bin/env bash
# Read-only environment inspection; no install, sync, restart or cleanup.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY=python3; fi
exec "$PY" "$ROOT/scripts/environment_status.py" "$@"
