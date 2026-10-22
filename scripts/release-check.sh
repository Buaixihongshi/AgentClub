#!/usr/bin/env bash
# Compatibility alias; lifecycle policy lives in env.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/check.sh" all "$@"
