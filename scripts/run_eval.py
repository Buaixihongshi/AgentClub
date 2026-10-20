#!/usr/bin/env python3
"""Run the A/B/C/D multi-agent evaluation and write a report.

Examples:
    # Harness validation, no model calls:
    python3 scripts/run_eval.py --mode fixture --repeats 1

    # Real CLI comparison (pins every agent to one provider):
    python3 scripts/run_eval.py --mode real --provider codex --repeats 3 --output output/eval/codex-2026-10-18

    # One category, two arms:
    python3 scripts/run_eval.py --mode real --provider codex --categories debugging --arms A,D --repeats 3
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.evaluation.runner import ARMS, EvalConfig, EvalRunner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["fixture", "fake", "real"], default="fixture")
    parser.add_argument("--tasks", type=Path, default=ROOT / "eval/tasks.json")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "eval/fixtures/scripted_answers.json",
        help="Fixture answers for --mode fixture",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--provider", default=None, help="Pin every agent to this provider (recommended for real runs)")
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--arms", default=",".join(ARMS), help="Comma-separated subset of A,B,C,D")
    parser.add_argument("--categories", default=None, help="Comma-separated category filter")
    parser.add_argument("--task-ids", default=None, help="Comma-separated task id filter")
    parser.add_argument("--lead", default="chulan", help="Lead agent id used by every arm")
    parser.add_argument(
        "--persona-mode",
        choices=["neutral", "project"],
        default="neutral",
        help="neutral strips persona voice for the architecture comparison; project uses config/agents.json",
    )
    parser.add_argument("--max-a2a", type=int, default=2, help="Dynamic A2A budget for arm D (capped at 2 host turns)")
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--cli-timeout", type=int, default=300)
    parser.add_argument("--resume", action="store_true", help="Skip trials already present in trials.jsonl")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    arms = [arm.strip().upper() for arm in args.arms.split(",") if arm.strip()]
    output = args.output or (
        ROOT
        / "output"
        / "eval"
        / "{stamp}-{suffix}".format(
            stamp=datetime.now().strftime("%Y%m%d-%H%M%S"),
            suffix=uuid4().hex[:4],
        )
    )
    config = EvalConfig(
        tasks_path=args.tasks,
        output_dir=output,
        mode=args.mode,
        fixture_path=args.fixture,
        provider=args.provider,
        config_dir=args.config_dir,
        repeats=max(1, args.repeats),
        seed=args.seed,
        arms=arms,
        categories=_split(args.categories),
        task_ids=_split(args.task_ids),
        lead_agent_id=args.lead,
        persona_mode=args.persona_mode,
        max_a2a=max(0, args.max_a2a),
        max_turns=max(1, args.max_turns),
        cli_timeout=max(1, args.cli_timeout),
        resume=args.resume,
        quiet=args.quiet,
    )
    runner = EvalRunner(config)
    summary = runner.run()

    if not args.quiet:
        print()
        print("Pairwise vs A (positive = arm better):")
        for comparison in summary.get("pairwise", []):
            print(
                "  {arm}: Δ={delta:+.3f} CI=[{low:+.3f}, {high:+.3f}] W/T/L={wins}/{ties}/{losses} p={p:.4f}".format(
                    arm=comparison["arm"],
                    delta=comparison["mean_delta"],
                    low=comparison["ci_low"],
                    high=comparison["ci_high"],
                    wins=comparison["wins"],
                    ties=comparison["ties"],
                    losses=comparison["losses"],
                    p=comparison["sign_test_p"],
                )
            )
        print()
        print("Key contrasts:")
        for comparison in summary.get("contrasts", []):
            print(
                "  {arm}−{baseline}: Δ={delta:+.3f} CI=[{low:+.3f}, {high:+.3f}] W/T/L={wins}/{ties}/{losses} p={p:.4f}".format(
                    baseline=comparison["baseline"],
                    arm=comparison["arm"],
                    delta=comparison["mean_delta"],
                    low=comparison["ci_low"],
                    high=comparison["ci_high"],
                    wins=comparison["wins"],
                    ties=comparison["ties"],
                    losses=comparison["losses"],
                    p=comparison["sign_test_p"],
                )
            )
        print()
        print("Report: {path}".format(path=output / "report.md"))
        print("Trials: {path}".format(path=output / "trials.jsonl"))
        print("Summary: {path}".format(path=output / "summary.json"))
    return 0


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


if __name__ == "__main__":
    raise SystemExit(main())
