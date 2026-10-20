import json
from pathlib import Path

import pytest

from backend.app.evaluation.scoring import score_answer
from backend.app.evaluation.tasks import load_tasks, select_tasks


ROOT = Path(__file__).resolve().parents[2]
TASKS_PATH = ROOT / "eval/tasks.json"
FIXTURES_PATH = ROOT / "eval/fixtures/scripted_answers.json"


def test_task_suite_covers_six_categories_with_multiple_tasks_each():
    task_set = load_tasks(TASKS_PATH)

    assert len(task_set.tasks) == 12
    counts = {}
    for task in task_set.tasks:
        counts[task.category] = counts.get(task.category, 0) + 1
        assert task.rubric.total_weight > 0
        assert 0.0 <= task.rubric.threshold <= 1.0
    assert set(counts) == {
        "simple_factual",
        "multi_constraint",
        "tradeoff_analysis",
        "debugging",
        "long_context",
        "ambiguous_requirement",
    }
    assert all(count >= 2 for count in counts.values())


def test_task_ids_are_unique_and_filters_work():
    task_set = load_tasks(TASKS_PATH)
    assert len({task.id for task in task_set.tasks}) == len(task_set.tasks)

    selected = select_tasks(task_set, categories=["debugging"])
    assert {task.category for task in selected} == {"debugging"}
    assert select_tasks(task_set, task_ids=["sf-01"])[0].id == "sf-01"
    with pytest.raises(ValueError):
        select_tasks(task_set, task_ids=["missing"])


def test_fixture_answers_cover_every_task_and_arm_with_expected_turn_counts():
    task_set = load_tasks(TASKS_PATH)
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["answers"]

    assert set(fixtures) == {task.id for task in task_set.tasks}
    for task in task_set.tasks:
        answers = fixtures[task.id]
        assert set(answers) == {"A", "B", "C", "D"}
        assert answers["A"]["final"].strip()
        for arm in "BCD":
            spec = answers[arm]
            assert spec["final"].strip()
            assert spec["draft"].strip()
            assert len(spec["checks"]) >= 2
            assert all(isinstance(item, str) and item.strip() for item in spec["checks"])
        dynamic = answers["D"].get("dynamic", [])
        delegations = answers["D"].get("delegations", [])
        assert len(dynamic) >= len(delegations)
        for delegation in delegations:
            assert delegation["when"] in {"起草", "检查一", "检查二", "修订"}
            assert delegation["target_agent_id"] in {"baobao", "wangye"}
            assert delegation["mode"] in {"fire_and_forget", "wait_reply"}


def test_fixture_answers_exercise_the_intended_regressions():
    """Guard rails for the transparent fixture demo, not model evidence."""
    task_set = load_tasks(TASKS_PATH)
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["answers"]

    def score(task_id, arm):
        task = task_set.by_id(task_id)
        return score_answer(fixtures[task_id][arm]["final"], task.rubric)

    assert score("sf-01", "A").passed
    assert not score("sf-01", "C").passed
    assert not score("sf-01", "D").passed

    assert not score("mc-02", "A").passed
    assert score("mc-02", "B").passed
    assert score("mc-02", "C").passed
    assert score("mc-02", "D").passed

    assert score("dbg-01", "A").passed
    assert not score("dbg-01", "C").passed
    assert score("dbg-01", "D").passed
