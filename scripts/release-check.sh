#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
RUN_REAL_CLI=false

usage() {
  echo "Usage: ./scripts/release-check.sh [--real-cli]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --real-cli) RUN_REAL_CLI=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ ! -x "$PY" ]; then
  echo "ERROR: .venv is missing. Run ./scripts/start.sh once first." >&2
  exit 1
fi

if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "ERROR: frontend/node_modules is missing. Run ./scripts/start.sh once first." >&2
  exit 1
fi

echo "[1/9] Backend tests"
cd "$ROOT"
"$PY" -m pytest backend/tests -q

echo "[2/9] Frontend tests"
(cd "$ROOT/frontend" && npm test)

echo "[3/9] Frontend production build"
(cd "$ROOT/frontend" && npm run build)

echo "[4/9] Fresh onboarding smoke (offline demo mode)"
"$PY" scripts/smoke_onboarding.py --fresh --fake

echo "[5/9] Conversation continuity smoke (offline demo mode)"
"$PY" scripts/smoke_conversation_flow.py --fake

echo "[6/9] Multi-agent mission smoke (offline demo mode)"
"$PY" scripts/smoke_real_cli_mission.py --fake

echo "[7/9] Python dependency consistency"
"$PY" -m pip check

echo "[8/9] Frontend dependency audit"
(cd "$ROOT/frontend" && npm audit)

echo "[9/9] Patch hygiene"
git diff --check

if [ "$RUN_REAL_CLI" = true ]; then
  echo
  echo "Running opt-in real CLI checks. Authenticated CLIs may incur model usage."
  "$PY" scripts/smoke_conversation_flow.py --real --timeout-seconds 420
  "$PY" scripts/smoke_real_cli_mission.py --real --timeout-seconds 300
fi

echo
echo "PASS: AgentClub release checks completed."
