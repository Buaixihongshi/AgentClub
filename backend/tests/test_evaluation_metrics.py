import pytest

from backend.app.evaluation.metrics import (
    TrialResult,
    arm_stats,
    bootstrap_mean_ci,
    sign_test_p,
    summarize,
)


def trial(task_id, arm, repeat, score, *, category="cat", tokens=100, status="completed"):
    return TrialResult(
        run_id="run",
        task_id=task_id,
        task_category=category,
        task_title=task_id,
        arm=arm,
        repeat=repeat,
        mission_id="m_{task}_{arm}_{repeat}".format(task=task_id, arm=arm, repeat=repeat),
        status=status,
        score=score,
        passed=score >= 0.8,
        failed_check_ids=[],
        checks=[],
        answer="answer",
        final_answer_chars=6,
        any_message_score=score,
        coverage_loss=0.0,
        agent_messages=1,
        turns_started=1,
        turns_completed=1,
        turns_blocked=0,
        turns_failed=0,
        a2a_invokes=0,
        a2a_wait_reply=0,
        a2a_skipped=0,
        tool_calls=0,
        tool_errors=0,
        provider_calls=1,
        input_tokens=tokens,
        output_tokens=0,
        total_tokens=tokens,
        cost_usd=None,
        token_source="estimate",
        latency_ms=10,
        wall_ms=10,
        prompt_chars=50,
        max_prompt_chars=50,
        response_chars=20,
        redundancy=0.0,
        duplicate_messages=0,
        started_at="2026-10-18T00:00:00+00:00",
        finished_at="2026-10-18T00:00:01+00:00",
        error="",
    )


def sample_trials():
    rows = []
    for repeat in (0, 1):
        rows.append(trial("t1", "A", repeat, 0.5, category="cat1", tokens=100))
        rows.append(trial("t2", "A", repeat, 0.5, category="cat2", tokens=100))
        rows.append(trial("t1", "B", repeat, 0.8, category="cat1", tokens=200))
        rows.append(trial("t2", "B", repeat, 0.6, category="cat2", tokens=200))
        rows.append(trial("t1", "C", repeat, 0.4, category="cat1", tokens=100))
        rows.append(trial("t2", "C", repeat, 0.5, category="cat2", tokens=300))
        rows.append(trial("t1", "D", repeat, 0.9, category="cat1", tokens=300))
        rows.append(trial("t2", "D", repeat, 0.9, category="cat2", tokens=300))
    return rows


def test_arm_stats_and_summary_are_paired_against_a():
    summary = summarize(sample_trials(), arms=("A", "B", "C", "D"), seed=7)

    assert summary["overall"]["A"]["mean_score"] == pytest.approx(0.5)
    assert summary["overall"]["B"]["mean_score"] == pytest.approx(0.7)
    assert summary["overall"]["C"]["mean_score"] == pytest.approx(0.45)
    assert summary["overall"]["D"]["mean_score"] == pytest.approx(0.9)

    by_arm = {item["arm"]: item for item in summary["pairwise"]}
    assert by_arm["B"]["n"] == 4
    assert by_arm["B"]["mean_delta"] == pytest.approx(0.2)
    assert by_arm["B"]["wins"] == 4
    assert by_arm["B"]["losses"] == 0
    assert by_arm["B"]["sign_test_p"] == pytest.approx(0.125)

    assert by_arm["C"]["mean_delta"] == pytest.approx(-0.05)
    assert (by_arm["C"]["wins"], by_arm["C"]["ties"], by_arm["C"]["losses"]) == (0, 2, 2)

    contrasts = {(item["baseline"], item["arm"]): item for item in summary["contrasts"]}
    assert set(contrasts) == {("A", "B"), ("B", "C"), ("C", "D")}
    assert contrasts[("B", "C")]["mean_delta"] == pytest.approx(-0.25)
    assert contrasts[("C", "D")]["mean_delta"] == pytest.approx(0.45)


def test_degradation_flags_category_and_task_regressions():
    summary = summarize(sample_trials(), arms=("A", "B", "C", "D"), seed=7)
    flags = summary["degradation"]

    assert any(
        flag["scope"] == "category"
        and flag["category"] == "cat1"
        and flag["arm"] == "C"
        and flag["reason"] == "quality_regression"
        for flag in flags
    )
    assert any(
        flag["scope"] == "task" and flag["task_id"] == "t1" and flag["arm"] == "C"
        for flag in flags
    )
    assert not any(flag["arm"] == "B" and flag["reason"] == "quality_regression" for flag in flags)


def test_pareto_marks_dominated_arm():
    summary = summarize(sample_trials(), arms=("A", "B", "C", "D"), seed=7)
    frontier = {point["arm"]: point for point in summary["pareto"]}

    assert frontier["A"]["pareto_optimal"] is True
    assert frontier["B"]["pareto_optimal"] is True
    assert frontier["C"]["pareto_optimal"] is False
    assert "A" in frontier["C"]["dominated_by"]


def test_bootstrap_is_deterministic_and_handles_constant_values():
    values = [0.1, 0.2, 0.3, 0.4]
    first = bootstrap_mean_ci(values, seed=42, samples=300)
    second = bootstrap_mean_ci(values, seed=42, samples=300)
    assert first == second
    assert first[0] <= first[1]

    assert bootstrap_mean_ci([0.5, 0.5], seed=1) == (0.5, 0.5)
    assert bootstrap_mean_ci([], seed=1) == (0.0, 0.0)


def test_sign_test_p_is_two_sided():
    assert sign_test_p(0, 0) == 1.0
    assert sign_test_p(5, 0) == pytest.approx(0.0625)
    assert sign_test_p(3, 3) == pytest.approx(1.0)


def test_arm_stats_handles_empty_and_failed_trials():
    assert arm_stats("A", []).n == 0

    failed = trial("t1", "A", 0, 0.0, status="failed", tokens=10)
    stats = arm_stats("A", [failed])
    assert stats.failure_rate == 1.0
    assert stats.total_cost_usd is None
    assert stats.tokens_per_success is None
    assert stats.cost_per_success_usd is None
    assert stats.cost_coverage == 0.0


def test_failed_mission_keeps_diagnostic_score_but_is_not_a_success(repository):
    from backend.app.adapters.fake import FakeCLIAdapter
    from backend.app.domain.models import Message, MessageRole, Mission, MissionStatus
    from backend.app.evaluation.adapters import MeteredAdapter
    from backend.app.evaluation.metrics import build_trial_result
    from backend.app.evaluation.tasks import EvalTask, Rubric, RubricCheck

    mission = Mission(
        id="m_failed_eval",
        title="failed",
        goal="answer",
        status=MissionStatus.FAILED,
    )
    repository.create_mission(mission)
    repository.add_message(
        Message(
            id="msg_failed_eval",
            mission_id=mission.id,
            role=MessageRole.AGENT,
            agent_id="chulan",
            content="ok",
        )
    )
    task = EvalTask(
        id="t_failed",
        category="cat",
        title="t",
        goal="answer",
        rubric=Rubric(
            checks=[RubricCheck(id="ok", kind="contains_any", values=["ok"])]
        ),
    )

    result = build_trial_result(
        run_id="run",
        task=task,
        arm="A",
        repeat=0,
        mission=repository.get_mission(mission.id),
        repository=repository,
        meter=MeteredAdapter(FakeCLIAdapter()),
        wall_ms=1,
        started_at="2026-10-18T00:00:00+00:00",
        finished_at="2026-10-18T00:00:01+00:00",
    )

    assert result.score == 1.0
    assert result.passed is False
    assert result.status == "failed"
