#!/usr/bin/env python3
"""Validate the six-task pilot and optionally exercise the existing offline harness.

This command never invokes real models. Cost figures are planning estimates,
not enforceable CLI spend limits or subscription invoices.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.evaluation.runner import EvalConfig, EvalRunner
from backend.app.evaluation.scoring import score_answer
from backend.app.evaluation.tasks import load_tasks

PACK = ROOT / "eval/pilots/cost-aware-v1"


def reference_answers() -> dict:
    """Compute small reference problems independently of rubric values."""
    records = [("t1", "done"), ("t2", "pending"), ("t3", "blocked"), ("t4", "pending")]
    pending = [key for key, status in records if status == "pending"]
    durations = {"A": 3, "B": 2, "C": 2, "D": 1}
    dependencies = {"A": [], "B": ["A"], "C": [], "D": ["B", "C"]}
    starts, ends, running, waiting = {}, {}, {}, set(durations)
    t = 0
    while waiting or running:
        for key in list(running):
            if running[key] <= t:
                ends[key] = running.pop(key)
        for key in sorted(waiting):
            if len(running) < 2 and all(parent in ends for parent in dependencies[key]):
                starts[key] = t
                running[key] = t + durations[key]
                waiting.remove(key)
        if running:
            t = min(running.values())
    evidence = [
        ("S1", "shanghai", 3, "2026-10-01", True),
        ("S2", "shanghai", 6, "2026-10-15", True),
        ("S3", "shanghai", 8, "2026-10-17", False),
        ("S4", "shenzhen", 4, "2026-10-02", True),
    ]
    effective = {}
    for source, city, value, date, active in sorted(evidence, key=lambda row: row[3]):
        if active and date <= "2026-10-18":
            effective[city] = (value, source)
    items = ["a", "b", "c"]
    cases = {"zero": 0, "negative": -1, "two": 2, "oversize": 9}
    expected = {key: items[max(0, len(items) - n):] if n > 0 else [] for key, n in cases.items()}
    failed = [key for key, n in cases.items() if items[-n:] != expected[key]]
    return {
        "cp-01": {"pending_ids": pending, "count": len(pending)},
        "cp-02": {"cost_microusd": int(Decimal(1000 - 400) * 2 + Decimal(400) * Decimal("0.5") + Decimal(200) * 8)},
        "cp-03": {"starts": starts, "finish": max(ends.values())},
        "cp-04": {"shanghai": effective["shanghai"][0], "shenzhen": effective["shenzhen"][0], "sources": [effective[city][1] for city in ("shanghai", "shenzhen")]},
        "cp-05": {"failed_cases": failed, "expected": expected},
        "cp-06": {"can_finish": None, "capacity": 2 * 20 * 3, "missing": ["remaining_tickets"]},
    }


def prepare(output: Path, *, run_fixture: bool = False) -> dict:
    protocol_path = PACK / "protocol.json"
    protocol = json.loads(protocol_path.read_text())
    tasks_path = PACK / protocol["tasks"]
    task_set = load_tasks(tasks_path)
    gold = reference_answers()
    ids = {task.id for task in task_set.tasks}
    if ids != set(gold):
        raise ValueError("reference answers and tasks differ")
    staged_ids = [key for stage in protocol["stages"] for key in stage["task_ids"]]
    if len(staged_ids) != len(set(staged_ids)) or set(staged_ids) != ids:
        raise ValueError("stages must partition the tasks without repeats")
    fixtures = {}
    for task in task_set.tasks:
        answer = json.dumps(gold[task.id], ensure_ascii=False)
        if not score_answer(answer, task.rubric).passed:
            raise ValueError("reference disagrees with rubric: " + task.id)
        if score_answer('{"incorrect":true}', task.rubric).passed:
            raise ValueError("rubric accepts unrelated output: " + task.id)
        fixtures[task.id] = {}
        for arm in "ABCD":
            spec = {"final": answer, "draft": answer, "checks": ["检查一：使用固定参考答案验证协议。", "检查二：使用固定参考答案验证格式。"]}
            if arm == "D":
                spec.update(dynamic=["离线接力：仅验证 wait_reply 往返。"], delegations=[{
                    "when": "检查二", "target_agent_id": "baobao", "purpose": "离线验证等待与恢复，不作为模型能力证据", "mode": "wait_reply",
                }])
            fixtures[task.id][arm] = spec

    policy = protocol["cost_policy"]
    per_turn = {}
    for key, model in protocol["models"].items():
        per_turn[key] = (
            Decimal(str(model["input_usd_per_million"])) * policy["planning_assumption_input_tokens_per_host_turn"]
            + Decimal(str(model["output_usd_per_million"])) * policy["planning_assumption_output_tokens_per_host_turn"]
        ) / 1_000_000
    worst_extra_model = max(per_turn, key=per_turn.get)
    condition_rows = []
    for condition in protocol["conditions"]:
        mapping = condition["mapping"]
        arm = condition["arm"]
        speakers = [protocol["lead"]] if arm == "A" else (
            [protocol["lead"]] * 4 if arm == "B" else ["chulan", "baobao", "wangye", "chulan"]
        )
        models = [mapping[speaker] for speaker in speakers]
        if arm == "D":
            models += [worst_extra_model] * policy["max_dynamic_host_turns"]
        estimate = sum((per_turn[model] for model in models), Decimal(0))
        condition_rows.append({"condition": condition["id"], "arm": arm, "max_host_turns": len(models), "planning_usd_per_task": float(estimate)})
    per_task_cost = sum((Decimal(str(row["planning_usd_per_task"])) for row in condition_rows), Decimal(0))
    stages = []
    for stage in protocol["stages"]:
        count = len(stage["task_ids"]) * protocol["repeats"]
        stages.append({"stage": stage["id"], "task_ids": stage["task_ids"], "trials": count * len(condition_rows), "max_host_turns": count * sum(row["max_host_turns"] for row in condition_rows), "planning_usd": float(count * per_task_cost)})
    manifest = {
        "mode": "offline_validation", "model_calls": 0,
        "tasks_sha256": hashlib.sha256(tasks_path.read_bytes()).hexdigest(),
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "reference_count": len(gold), "conditions": condition_rows, "stages": stages,
        "total_planned_trials": sum(stage["trials"] for stage in stages),
        "total_planning_usd": float(sum((Decimal(str(stage["planning_usd"])) for stage in stages), Decimal(0))),
        "pricing_basis": "API-equivalent estimate; no cache discounts; not CLI invoice or hard bound",
        "real_run_ready": False,
        "remaining_requirements": ["Set an explicit total spend limit", "Collect complete usage and guard spend between calls", "Use an input-only isolated workspace; never expose rubric/gold", "Pin and audit CLI tool permissions and real model selection"],
    }
    output.mkdir(parents=True, exist_ok=True)
    fixture_path = output / "scripted_answers.json"
    fixture_path.write_text(json.dumps({"answers": fixtures}, ensure_ascii=False, indent=2) + "\n")
    if run_fixture:
        summary = EvalRunner(EvalConfig(tasks_path=tasks_path, fixture_path=fixture_path, output_dir=output / "fixture", config_dir=ROOT / "config", mode="fixture", repeats=1, seed=protocol["seed"], quiet=True)).run()
        manifest["offline_fixture_trials"] = sum(row["n"] for row in summary["overall"].values())
        manifest["offline_fixture_report"] = str(output / "fixture/report.md")
    (output / "plan.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/eval/cost-aware-v1")
    parser.add_argument("--run-fixture", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, run_fixture=args.run_fixture), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
