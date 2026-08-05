#!/usr/bin/env python3
"""Fail when a release tree contains local runtime data or secret-like text.

Only category and relative path are printed. Matched values are never echoed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "__pycache__", ".pytest_cache"}
FORBIDDEN_DIRS = {
    ".yiren",
    ".trellis",
    ".claude",
    ".codex",
    ".antigravitycli",
    ".playwright-cli",
    ".superpowers",
    "data",
    "artifacts",
    "output",
}
FORBIDDEN_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".log", ".pem", ".key", ".p12", ".pfx"}
SECRET_PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "openai-key": re.compile(rb"sk-[A-Za-z0-9_-]{16,}"),
    "github-token": re.compile(rb"(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}"),
    "slack-token": re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "google-key": re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    "aws-key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "personal-home": re.compile(rb"(?:/Users/|/home/)[^/\s\"']+"),
}


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def main() -> int:
    findings: list[tuple[str, str]] = []
    for path in iter_files():
        relative = path.relative_to(ROOT)
        parts = set(relative.parts)
        if parts & FORBIDDEN_DIRS:
            if relative.as_posix() != "artifacts/.gitkeep":
                findings.append(("forbidden-runtime-path", relative.as_posix()))
            continue
        if path.name == ".env" or path.name.startswith(".env."):
            if path.name != ".env.example":
                findings.append(("environment-file", relative.as_posix()))
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(("sensitive-file-type", relative.as_posix()))
            continue
        try:
            content = path.read_bytes()
        except OSError:
            findings.append(("unreadable-file", relative.as_posix()))
            continue
        if b"\x00" in content[:4096]:
            continue
        for category, pattern in SECRET_PATTERNS.items():
            if relative.as_posix() == "scripts/privacy-check.py" and category == "personal-home":
                continue
            if pattern.search(content):
                findings.append((category, relative.as_posix()))

    if findings:
        print("FAIL: privacy check found release blockers:")
        for category, relative in sorted(set(findings)):
            print(f"- {category}: {relative}")
        return 1
    print("PASS: no local runtime data, personal home paths, or known secret patterns found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
