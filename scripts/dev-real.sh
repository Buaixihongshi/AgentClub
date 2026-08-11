#!/usr/bin/env bash
# Launch the AgentClub full stack with REAL CLI providers for manual verification.
#
# Why: the real-provider path must NOT use the committed config/cli-providers.json
# (its real providers stay enabled:false on purpose — see CLAUDE.md "MCP transport
# rules"). This script copies config/ into a throwaway temp dir, flips the requested
# providers to enabled there, points the backend at it via YIREN_CONFIG_DIR, and
# launches backend (--reload, reusing dev-backend.sh) + frontend (vite) together,
# tearing both processes and the temp config down on exit.
#
# Usage:
#   scripts/dev-real.sh                          # all real providers
#   PROVIDERS=claude,codex,antigravity scripts/dev-real.sh
#   PORT=8001 FRONTEND_PORT=5174 scripts/dev-real.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROVIDERS="${PROVIDERS:-claude,codex,antigravity}"
BACKEND_HOST="${HOST:-127.0.0.1}"
BACKEND_PORT="${PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  echo "ERROR: .venv missing. Run: python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# Port pre-flight for BOTH ports before we touch anything — fail loud, fail early.
for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ERROR: port $port already in use. Existing listener:" >&2
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2 || true
    echo "Stop it (e.g. 'kill <pid>') and re-run, or override the port env var." >&2
    exit 1
  fi
done

# Build the temp config dir. Copies config/ verbatim (so agents.json comes along)
# then enables ONLY the requested providers — committed config is never written.
CONFIG_TMP="$(mktemp -d "${TMPDIR:-/tmp}/yiren-real-config.XXXXXX")"
PROVIDERS="$PROVIDERS" "$PY" - "$CONFIG_TMP" <<'PY'
import json, os, shutil, sys
from pathlib import Path

dest = Path(sys.argv[1])
shutil.copytree(Path("config"), dest, dirs_exist_ok=True)

requested = {p.strip() for p in os.environ["PROVIDERS"].split(",") if p.strip()}
pp = dest / "cli-providers.json"
providers = json.loads(pp.read_text(encoding="utf-8"))
known = {p["id"] for p in providers}

unknown = requested - known
if unknown:
    sys.exit("Unknown provider id(s): {u}. Known: {k}".format(
        u=", ".join(sorted(unknown)), k=", ".join(sorted(known))))

real = sorted(requested - {"fake"})
if not real:
    sys.exit("PROVIDERS must include at least one real provider (got: {r})".format(
        r=", ".join(sorted(requested)) or "<empty>"))

for prov in providers:
    prov["enabled"] = prov["id"] in requested
    if prov["enabled"] and prov["id"] != "fake":
        prov["mcp_enabled"] = True
pp.write_text(json.dumps(providers, indent=2, ensure_ascii=False), encoding="utf-8")
print("Real providers enabled: " + ", ".join(real))
PY

echo "Temp config dir: $CONFIG_TMP"

BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
  echo
  echo "Shutting down AgentClub dev stack…"
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
  rm -rf "$CONFIG_TMP"
}
trap cleanup EXIT INT TERM

# Backend: reuse dev-backend.sh so we inherit its --reload guarantee. The real-CLI
# switch is carried in via these exported env vars.
export YIREN_USE_FAKE_CLI=false
export YIREN_CONFIG_DIR="$CONFIG_TMP"
HOST="$BACKEND_HOST" PORT="$BACKEND_PORT" scripts/dev-backend.sh &
BACKEND_PID=$!
echo "Backend (real providers) pid=$BACKEND_PID  http://$BACKEND_HOST:$BACKEND_PORT"

# Frontend: vite dev server proxies /api -> backend.
( cd frontend && npm run dev -- --port "$FRONTEND_PORT" --strictPort ) &
FRONTEND_PID=$!
echo "Frontend pid=$FRONTEND_PID  http://localhost:$FRONTEND_PORT"

echo
echo "Open http://localhost:$FRONTEND_PORT  (Ctrl-C to stop both)"
wait
