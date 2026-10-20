#!/usr/bin/env python3
"""Verify the frozen pack and author-written references; no model calls."""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.evaluation.code_execution import grade_stdin


def validate(pack):
    pack = Path(pack)
    manifest = json.loads((pack / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if hashlib.sha256((pack / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen pack file changed: " + name)
    spec = importlib.util.spec_from_file_location("grader_references", ROOT / "eval/pilots/open-code-v1/grader_references.py")
    references = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(references)
    results, mutants = [], []
    for task in manifest["tasks"]:
        task_id = task["question_id"]
        public = json.loads((pack / "public" / f"{task_id}.json").read_text())
        assert not {"private_test_cases", "canonical_solution", "focus", "family"} & set(public)
        cases = json.loads((pack / "private" / f"{task_id}.json").read_text())
        row = {"task_id": task_id}
        for category in ("public", "private"):
            row[category] = grade_stdin(references.REFERENCES[task_id], cases[f"{category}_test_cases"])
        row["wrong_program_rejected"] = not grade_stdin("print('__wrong__')", cases["public_test_cases"][:1])["passed"]
        results.append(row)
        print(task_id, {category: f"{row[category]['passed_tests']}/{row[category]['total_tests']}" for category in ("public", "private")}, flush=True)
    for name, (task_id, code) in references.MUTANTS.items():
        cases = json.loads((pack / "private" / f"{task_id}.json").read_text())
        mutants.append({"name": name, "task_id": task_id,
                        "public": grade_stdin(code, cases["public_test_cases"]),
                        "private": grade_stdin(code, cases["private_test_cases"])})
    result = {"model_calls": 0, "sandbox": "macOS sandbox-exec", "references": results, "mutants": mutants,
              "all_references_pass": all(row["public"]["passed"] and row["private"]["passed"] and row["wrong_program_rejected"] for row in results)}
    with (pack / "validation.json").open("x") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=ROOT / "output/eval/open-code-v1")
    args = parser.parse_args()
    result = validate(args.pack)
    print(json.dumps({"model_calls": 0, "all_references_pass": result["all_references_pass"],
                      "mutants": [{"name": row["name"], "public_passed": row["public"]["passed"], "hidden_passed": row["private"]["passed"]} for row in result["mutants"]]}, indent=2))
    raise SystemExit(0 if result["all_references_pass"] else 1)
