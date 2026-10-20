#!/usr/bin/env python3
"""First real cost pilot: --run executes 12 trials; default is dry-run."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.evaluation.real_pilot import run_pilot

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "output/eval" / ("real-pilot-" + datetime.now().strftime("%Y%m%d-%H%M%S")))
    parser.add_argument("--limit-usd", type=float, default=1.25)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--prior-usd", type=float, default=0, help="Carry forward protocol-debugging costs into this batch's spend gate")
    args = parser.parse_args()
    if args.limit_usd <= 0 or args.timeout <= 0 or args.prior_usd < 0:
        parser.error("limit and timeout must be positive")
    result = run_pilot(args.output, limit_usd=args.limit_usd, execute=args.run, timeout=args.timeout, prior_usd=args.prior_usd)
    print(json.dumps({key: result.get(key) for key in ("mode", "status", "completed_trials", "observed_host_calls", "observed_api_equivalent_usd", "stop_reason")}, ensure_ascii=False, indent=2))
