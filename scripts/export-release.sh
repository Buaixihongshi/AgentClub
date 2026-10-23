#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-}"

usage() {
  echo "Usage: ./scripts/export-release.sh /absolute/path/to/release-repository"
}

if [ -z "$TARGET" ] || [ "${TARGET#/}" = "$TARGET" ]; then
  echo "ERROR: provide an absolute target path." >&2
  usage >&2
  exit 2
fi

if [ -e "$TARGET" ]; then
  echo "ERROR: target already exists: $TARGET" >&2
  exit 1
fi

mkdir -p "$TARGET/docs/architecture" "$TARGET/artifacts"

for file in .gitignore LICENSE README.md README_EN.md CHANGELOG.md requirements.txt CONTRIBUTING.md AGENTS.md .python-version .nvmrc; do
  cp "$ROOT/$file" "$TARGET/$file"
done

for directory in backend config frontend scripts docs .github; do
  rsync -a \
    --exclude '.DS_Store' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude 'node_modules/' \
    --exclude 'dist/' \
    --exclude '*.tsbuildinfo' \
    "$ROOT/$directory/" "$TARGET/$directory/"
done

cp "$ROOT/artifacts/.gitkeep" "$TARGET/artifacts/.gitkeep"

python3 "$TARGET/scripts/privacy-check.py"
git -C "$TARGET" init -b main >/dev/null

echo "PASS: clean release repository created at $TARGET"
echo "The repository has no commits and no remote. Review it before the first commit."
