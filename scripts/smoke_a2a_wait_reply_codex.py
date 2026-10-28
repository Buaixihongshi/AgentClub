#!/usr/bin/env python3
"""Smoke: A2A wait_reply round-trip.

This is a focused entrypoint for the G4 wait_reply acceptance path. The broader
`smoke_a2a_codex.py` script still covers both fire-and-forget and wait_reply.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from smoke_a2a_codex import run_wait_reply  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Unity A2A wait_reply smoke")
    parser.add_argument("--fake", action="store_true", help="Run fake-mode wait_reply smoke")
    parser.add_argument("--real", action="store_true", help="Reserved for future real CLI smoke")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args()

    if args.real:
        print("Real wait_reply smoke is not implemented yet; use --fake.")
        return 2
    return run_wait_reply(args.timeout_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
