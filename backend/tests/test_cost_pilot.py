import json

import pytest

from backend.app.evaluation.scoring import score_answer
from backend.app.evaluation.tasks import Rubric, RubricCheck, load_tasks
from scripts.prepare_cost_pilot import PACK, prepare, reference_answers
from backend.app.adapters.stream_parsers import StreamLineParser


def test_exact_json_accepts_key_order_and_whitespace_without_coercing_types():
    rubric = Rubric(threshold=1, checks=[RubricCheck(
        id="exact", kind="json_exact", value={"count": 1, "state": "OK", "items": ["A", "B"]},
    )])
    assert score_answer('{ "items": ["A", "B"], "state": "OK", "count": 1 }', rubric).passed
    for payload in [
        {"count": True, "state": "OK", "items": ["A", "B"]},
        {"count": "1", "state": "OK", "items": ["A", "B"]},
        {"count": 1, "state": "ok", "items": ["A", "B"]},
        {"count": 1, "state": "OK", "items": ["B", "A"]},
        {"count": 1, "state": "OK", "items": ["A", "B"], "extra": 1},
    ]:
        assert not score_answer(json.dumps(payload), rubric).passed


@pytest.mark.parametrize("answer", [
    '{"count":0,"count":1}', '{"count":NaN}', '{"count":Infinity}',
    '```json\n{"count":1}\n```', '答案：{"count":1}', '{"count":1} trailing',
    '[{"count":1}]', '{}', '{"count":1.0}',
])
def test_exact_json_rejects_ambiguous_or_noncontract_outputs(answer):
    rubric = Rubric(threshold=1, checks=[RubricCheck(id="exact", kind="json_exact", value={"count": 1})])
    assert not score_answer(answer, rubric).passed


def test_reference_problems_agree_with_rubrics_and_reject_realistic_mistakes():
    tasks = load_tasks(PACK / "tasks.json")
    gold = reference_answers()
    mistakes = {
        "cp-01": {"pending_ids": ["t2", "t3", "t4"], "count": 3},
        "cp-02": {"cost_microusd": 3800},  # charges cached input twice
        "cp-03": {"starts": {"A": 0, "B": 3, "C": 0, "D": 3}, "finish": 5},
        "cp-04": {"shanghai": 8, "shenzhen": 4, "sources": ["S3", "S4"]},
        "cp-05": {**gold["cp-05"], "failed_cases": []},
        "cp-06": {**gold["cp-06"], "can_finish": True},
    }
    for task in tasks.tasks:
        assert score_answer(json.dumps(gold[task.id]), task.rubric).passed
        assert not score_answer(json.dumps(mistakes[task.id]), task.rubric).passed


def test_cost_plan_counts_all_conditions_dynamic_turns_and_stages(tmp_path):
    plan = prepare(tmp_path)
    assert plan["total_planned_trials"] == 36
    assert [stage["trials"] for stage in plan["stages"]] == [12, 24]
    assert [stage["max_host_turns"] for stage in plan["stages"]] == [40, 80]
    assert plan["total_planning_usd"] == pytest.approx(3.07125)
    assert plan["stages"][0]["planning_usd"] == pytest.approx(1.02375)
    assert plan["real_run_ready"] is False
    assert plan["model_calls"] == 0
    assert len(plan["tasks_sha256"]) == 64


def test_pilot_fixture_exercises_all_arms_and_wait_reply_without_paid_calls(tmp_path):
    plan = prepare(tmp_path, run_fixture=True)
    assert plan["offline_fixture_trials"] == 24
    trials = [json.loads(line) for line in (tmp_path / "fixture/trials.jsonl").read_text().splitlines()]
    assert len(trials) == 24
    assert all(trial["passed"] for trial in trials)
    assert all(trial["turns_started"] == 6 and trial["a2a_wait_reply"] == 1 for trial in trials if trial["arm"] == "D")


@pytest.mark.parametrize("status", ["SUCCESS", "ERROR"])
def test_antigravity_accounts_output_thinking_and_cache_without_coercion(status):
    raw = {"input_tokens": 10415, "output_tokens": 657, "thinking_tokens": 616, "cache_read_tokens": 8113, "total_tokens": 11072}
    event = StreamLineParser("antigravity").parse(json.dumps({"event": "result", "result": {"status": status, "usage": raw}}))
    assert event.usage.input_tokens == 10415
    assert event.usage.output_tokens == 657  # includes thinking; do not add 616 again
    assert event.usage.reasoning_output_tokens == 616
    assert event.usage.cached_input_tokens == 8113
    assert event.usage.provider_raw == raw


@pytest.mark.parametrize("raw", [None, {}, {"input_tokens": 1}, {"input_tokens": True, "output_tokens": 2}, {"input_tokens": -1, "output_tokens": 2}])
def test_antigravity_missing_or_invalid_usage_stays_unknown(raw):
    event = StreamLineParser("antigravity").parse(json.dumps({"event": "result", "result": {"status": "SUCCESS", "usage": raw}}))
    assert event.usage is None
