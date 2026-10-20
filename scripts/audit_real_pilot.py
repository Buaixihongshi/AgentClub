#!/usr/bin/env python3
"""Re-score public first drafts and audit persisted A2A events; no model calls."""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.evaluation.scoring import score_answer
from backend.app.evaluation.tasks import load_tasks


def audit(source: Path, tasks_path: Path) -> dict:
    tasks = load_tasks(tasks_path)
    trials = [json.loads(line) for line in (source / "trials.jsonl").read_text().splitlines() if line.strip()]
    by_mission = {trial["mission_id"]: trial for trial in trials}
    if len(by_mission) != len(trials):
        raise ValueError("Duplicate mission IDs in trials.jsonl")
    records = []
    for database in sorted(source.glob("*/eval.sqlite3")):
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            events = [(kind, json.loads(payload)) for kind, payload in connection.execute(
                "SELECT event_type, payload FROM events "
                "WHERE event_type IN ('mission_created', 'turn.started', 'tool.call') "
                "OR event_type LIKE 'a2a.%' ORDER BY id"
            )]
            messages = [json.loads(data) for (data,) in connection.execute(
                "SELECT data FROM messages ORDER BY rowid"
            )]
        created = [payload for kind, payload in events if kind == "mission_created"]
        if len(created) != 1 or created[0]["id"] not in by_mission:
            raise ValueError(f"Cannot uniquely match database to trial: {database}")
        trial = by_mission[created[0]["id"]]
        public = sorted((message for message in messages if message["role"] == "agent"),
                        key=lambda message: message["created_at"])
        counts = Counter(kind for kind, _ in events)
        tools = Counter(payload.get("tool_name") for kind, payload in events if kind == "tool.call")
        first = score_answer(public[0]["content"], tasks.by_id(trial["task_id"]).rubric) if public else None
        records.append({
            "trial": database.parent.name,
            "database": str(database.relative_to(source)),
            "mission_id": trial["mission_id"],
            "task_id": trial["task_id"],
            "condition": trial["condition"],
            "first_answer_passed": first.passed if first else None,
            "first_answer_score": first.score if first else None,
            "final_answer_passed": trial["passed"],
            "public_agent_messages": len(public),
            "turns_started": counts["turn.started"],
            "invoke_agent_tool_calls": tools["invoke_agent"],
            "a2a_events": {kind: count for kind, count in counts.items() if kind.startswith("a2a.")},
            "public_speaker_sequence": [message["agent_id"] for message in public],
        })
    if {row["mission_id"] for row in records} != set(by_mission) or len(records) != len(trials):
        raise ValueError("Incomplete or duplicate trial/database coverage")
    multi = [row for row in records if row["condition"].startswith(("B-", "C-", "D-"))]
    dynamic = [row for row in records if row["condition"].startswith("D-")]
    return {
        "source": str(source.resolve()),
        "task_file": str(tasks_path.resolve()),
        "model_calls_performed_by_audit": 0,
        "independent_tasks": len({row["task_id"] for row in records}),
        "trials": len(records),
        "multiturn_trials": len(multi),
        "multiturn_first_drafts_passed": sum(row["first_answer_passed"] is True for row in multi),
        "dynamic_trials": len(dynamic),
        "dynamic_invoke_agent_tool_calls": sum(row["invoke_agent_tool_calls"] for row in dynamic),
        "dynamic_a2a_requests": sum(row["a2a_events"].get("a2a.request", 0) for row in dynamic),
        "dynamic_a2a_invokes": sum(row["a2a_events"].get("a2a.invoke", 0) for row in dynamic),
        "records": records,
        "interpretation": "First-draft scores are diagnostic only; final-answer selection is unchanged. Zero requests cannot establish the effect of executing A2A.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=ROOT / "eval/pilots/cost-aware-v1/tasks.json")
    parser.add_argument("--output", type=Path, help="New analysis JSON path; default prints JSON only")
    args = parser.parse_args()
    result = audit(args.input, args.tasks)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        # Never replace an earlier audit or a raw experiment file.
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
