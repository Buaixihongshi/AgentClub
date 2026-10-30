#!/usr/bin/env python3
"""Run bounded bidirectional A2A probes; model calls require --run."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import extensions  # noqa: E402  (same optional extensions as main.py)

extensions.install()
from backend.app.evaluation.a2a_probe import run_probes

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "output/eval" / ("a2a-probes-" + datetime.now().strftime("%Y%m%d-%H%M%S")))
    parser.add_argument("--limit-usd", type=float, default=1.5)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if args.limit_usd <= 0 or args.timeout <= 0:
        parser.error("limit and timeout must be positive")
    result = run_probes(args.output, execute=args.run, limit_usd=args.limit_usd, timeout=args.timeout)
    print(json.dumps({key: result.get(key) for key in ("mode", "status", "passed_probes", "observed_host_calls", "observed_api_equivalent_usd", "stop_reason")}, ensure_ascii=False, indent=2))
