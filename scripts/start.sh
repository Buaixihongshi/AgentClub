#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

NO_INSTALL=false
NO_OPEN=false
RESET_ONBOARDING=false
RELOAD_BACKEND=false

usage() {
  echo "Usage: ./scripts/start.sh [--no-install] [--no-open] [--reset-onboarding] [--reload]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-install) NO_INSTALL=true ;;
    --no-open) NO_OPEN=true ;;
    --reset-onboarding) RESET_ONBOARDING=true ;;
    --reload) RELOAD_BACKEND=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

BOOT_HOST="${HOST:-127.0.0.1}"
BACKEND_PORT="${PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_ORIGIN="http://${BOOT_HOST}:${BACKEND_PORT}"
APP_URL="http://${BOOT_HOST}:${FRONTEND_PORT}"
RUNTIME_DIR="${YIREN_RUNTIME_DIR:-$ROOT/.yiren}"

for command_name in python3 node npm; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERROR: $command_name is required to start AgentClub." >&2
    echo "Install it, then run ./scripts/start.sh again." >&2
    exit 1
  fi
done

if ! node -e '
const [major, minor] = process.versions.node.split(".").map(Number);
const supported = (major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22;
process.exit(supported ? 0 : 1);
'; then
  echo "ERROR: Node.js 20.19+ or 22.12+ is required (current: $(node --version))." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR"

if [ "$NO_INSTALL" = true ]; then
  if [ ! -x "$ROOT/.venv/bin/python" ]; then
    echo "ERROR: .venv is missing. Run ./scripts/start.sh once without --no-install." >&2
    exit 1
  fi
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    echo "ERROR: frontend/node_modules is missing. Run ./scripts/start.sh once without --no-install." >&2
    exit 1
  fi
else
  if [ ! -x "$ROOT/.venv/bin/python" ]; then
    echo "Preparing Python environment…"
    python3 -m venv "$ROOT/.venv"
  fi

  REQUIREMENTS_HASH="$(cksum "$ROOT/requirements.txt" | awk '{print $1 ":" $2}')"
  REQUIREMENTS_STAMP="$RUNTIME_DIR/requirements.stamp"
  if [ ! -f "$REQUIREMENTS_STAMP" ] || [ "$(sed -n '1p' "$REQUIREMENTS_STAMP")" != "$REQUIREMENTS_HASH" ]; then
    echo "Installing backend dependencies…"
    "$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"
    printf '%s\n' "$REQUIREMENTS_HASH" > "$REQUIREMENTS_STAMP"
  fi

  PACKAGE_HASH="$(cksum "$ROOT/frontend/package-lock.json" | awk '{print $1 ":" $2}')"
  PACKAGE_STAMP="$RUNTIME_DIR/package-lock.stamp"
  if [ ! -d "$ROOT/frontend/node_modules" ] || [ ! -f "$PACKAGE_STAMP" ] || [ "$(sed -n '1p' "$PACKAGE_STAMP")" != "$PACKAGE_HASH" ]; then
    echo "Installing frontend dependencies…"
    (cd "$ROOT/frontend" && npm ci)
    printf '%s\n' "$PACKAGE_HASH" > "$PACKAGE_STAMP"
  fi
fi

PY="$ROOT/.venv/bin/python"

check_port() {
  local label="$1"
  local port="$2"
  if ! "$PY" - "$BOOT_HOST" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(0.5)
try:
    in_use = sock.connect_ex((host, port)) == 0
finally:
    sock.close()
raise SystemExit(1 if in_use else 0)
PY
  then
    echo "ERROR: $label port $port is already in use." >&2
    if command -v lsof >/dev/null 2>&1; then
      lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2 || true
    fi
    echo "Stop the existing process, or choose another port and run again." >&2
    exit 1
  fi
}

check_port "Backend" "$BACKEND_PORT"
check_port "Frontend" "$FRONTEND_PORT"

BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
  trap - EXIT INT TERM
  if [ -n "$FRONTEND_PID" ]; then
    kill "$FRONTEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [ -n "$BACKEND_PID" ]; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "Starting AgentClub backend…"
BACKEND_ARGS=(--host "$BOOT_HOST" --port "$BACKEND_PORT")
if [ "$RELOAD_BACKEND" = true ]; then
  BACKEND_ARGS+=(--reload)
fi
PYTHONPATH="$ROOT" "$PY" -m uvicorn backend.app.main:app "${BACKEND_ARGS[@]}" \
  >"$RUNTIME_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

echo "Starting AgentClub frontend…"
(
  cd "$ROOT/frontend"
  YIREN_BACKEND_ORIGIN="$BACKEND_ORIGIN" npm run dev -- \
    --host "$BOOT_HOST" --port "$FRONTEND_PORT" --strictPort
) >"$RUNTIME_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

if ! "$PY" - "$APP_URL/api/health" <<'PY'
import json
import sys
import time
import urllib.request

url = sys.argv[1]
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            payload = json.load(response)
        if payload.get("status") == "ok":
            raise SystemExit(0)
    except Exception:
        time.sleep(0.25)
raise SystemExit(1)
PY
then
  echo "ERROR: AgentClub did not become healthy within 30 seconds." >&2
  echo "Backend log:" >&2
  tail -n 30 "$RUNTIME_DIR/backend.log" >&2 || true
  echo "Frontend log:" >&2
  tail -n 30 "$RUNTIME_DIR/frontend.log" >&2 || true
  exit 1
fi

if [ "$RESET_ONBOARDING" = true ]; then
  "$PY" - "$BACKEND_ORIGIN/api/onboarding/reset" <<'PY'
import sys
import urllib.request

request = urllib.request.Request(sys.argv[1], data=b"", method="POST")
with urllib.request.urlopen(request, timeout=5):
    pass
PY
fi

echo
echo "AgentClub is ready: $APP_URL"
echo "Logs: $RUNTIME_DIR/backend.log and $RUNTIME_DIR/frontend.log"
echo "Press Ctrl-C to stop both services."

if [ "$NO_OPEN" = false ]; then
  if command -v open >/dev/null 2>&1; then
    open "$APP_URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$APP_URL" >/dev/null 2>&1 || true
  fi
fi

while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done

echo "ERROR: one of the AgentClub services stopped unexpectedly." >&2
echo "Check $RUNTIME_DIR/backend.log and $RUNTIME_DIR/frontend.log." >&2
exit 1
