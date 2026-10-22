#!/usr/bin/env bash
# Launch the AgentClub backend in development mode with auto-reload.
# Why: bare `uvicorn ...` invocations (without --reload) leave stale processes
# serving old code; this wrapper enforces reload + a port pre-flight so code
# edits land immediately and conflicts surface loudly.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3 || command -v python)"
  if [ -z "$PY" ]; then
    echo "ERROR: python3 not found and .venv missing. Run: python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt" >&2
    exit 1
  fi
  echo "WARN: .venv not found, falling back to $PY (run 'python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt' to use a venv)" >&2
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: port $PORT already in use. Existing listener:" >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2 || true
  echo "Stop it (e.g. 'kill <pid>') and re-run, or set PORT=<other> $0" >&2
  exit 1
fi

export PYTHONPATH="$ROOT"
export YIREN_ENVIRONMENT="${YIREN_ENVIRONMENT:-development}"
exec "$PY" -m uvicorn backend.app.main:app \
  --host "$HOST" --port "$PORT" --reload "$@"
