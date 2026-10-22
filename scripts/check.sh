#!/usr/bin/env bash
# Compatibility alias; lifecycle policy lives in env.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then PY=python3; fi
exec "$PY" "$ROOT/scripts/checks.py" "$@"
