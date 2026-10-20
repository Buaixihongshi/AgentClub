#!/usr/bin/env python3
"""Grade one final Python submission against the prepared subset; no model calls."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.evaluation.code_execution import grade_stdin

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=ROOT / "output/eval/open-code-v1")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--public-only", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((args.pack / "manifest.json").read_text())
    if args.task_id not in {row["question_id"] for row in manifest["tasks"]}:
        parser.error("Task ID is not in the pinned pack")
    cases = json.loads((args.pack / "private" / f"{args.task_id}.json").read_text())
    code = args.candidate.read_text()
    result = {"task_id": args.task_id, "public": grade_stdin(code, cases["public_test_cases"])}
    if not args.public_only:
        result["private"] = grade_stdin(code, cases["private_test_cases"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
