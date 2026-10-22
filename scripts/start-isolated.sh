#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENVIRONMENT="${1:-}"
case "$ENVIRONMENT" in
  development) PORT=8100; FRONTEND_PORT=5174 ;;
  test) PORT=8200; FRONTEND_PORT=5175 ;;
  *) echo "Usage: $0 development|test [start.sh options]" >&2; exit 2 ;;
esac
shift
export PORT FRONTEND_PORT
export HOST=127.0.0.1
export YIREN_ENVIRONMENT="$ENVIRONMENT"
STATE="$ROOT/.yiren/$ENVIRONMENT"
mkdir -p "$STATE/config" "$STATE/artifacts" "$STATE/data"
for file in agents.json cli-providers.json; do
  if [ ! -f "$STATE/config/$file" ]; then
    cp "$ROOT/config/$file" "$STATE/config/$file"
  fi
done
export YIREN_CONFIG_DIR="$STATE/config"
export YIREN_DATABASE_PATH="$STATE/data/yirenhub.sqlite3"
export YIREN_ARTIFACT_ROOT="$STATE/artifacts"
export YIREN_MAINTENANCE_WORKTREE_ROOT="$STATE/maintenance-worktrees"
export YIREN_USE_FAKE_CLI=true
export YIREN_MAINTENANCE_ENABLED=false
export YIREN_RUNTIME_DIR="$STATE/runtime"
git -C "$ROOT" rev-parse HEAD > "$STATE/checkout-sha"
exec bash "$ROOT/scripts/start.sh" --no-open "$@"
