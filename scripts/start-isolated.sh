#!/usr/bin/env bash
# Compatibility alias; lifecycle policy lives in env.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${1:-}"
case "$PROFILE" in development|test) shift ;; *) echo "Usage: $0 development|test [options]" >&2; exit 2 ;; esac
exec "$ROOT/scripts/env.sh" start "$PROFILE" "$@"
