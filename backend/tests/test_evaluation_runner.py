import json
from pathlib import Path

import pytest

from backend.app.domain.agent_registry import AgentRegistry
from backend.app.evaluation.runner import EvalConfig, EvalRunner, _neutral_registry


ROOT = Path(__file__).resolve().parents[2]


def _config(tmp_path, **updates):
    values = dict(
        tasks_path=ROOT / "eval/tasks.json",
        fixture_path=ROOT / "eval/fixtures/scripted_answers.json",
        output_dir=tmp_path / "eval",
        mode="fixture",
        config_dir=ROOT / "config",
        repeats=1,
        seed=3,
        arms=("A", "B", "C", "D"),
        task_ids=["sf-01", "mc-01"],
        quiet=True,
    )
    values.update(updates)
    return EvalConfig(**values)


def test_fixture_runner_writes_trials_summary_and_report(tmp_path):
    runner = EvalRunner(_config(tmp_path))

    summary = runner.run()

    output = tmp_path / "eval"
    assert (output / "trials.jsonl").exists()
    assert (output / "trials.csv").exists()
    assert (output / "summary.json").exists()
    assert (output / "report.md").exists()

    lines = [
        json.loads(line)
        for line in (output / "trials.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 8
    assert {line["arm"] for line in lines} == {"A", "B", "C", "D"}
    assert all(line["status"] == "completed" for line in lines)

    by_arm = {line["arm"]: line for line in lines if line["task_id"] == "sf-01"}
    assert by_arm["A"]["turns_started"] == 1
    assert by_arm["B"]["turns_started"] == 4
    assert by_arm["C"]["turns_started"] == 4
    assert by_arm["D"]["turns_started"] == 5
    assert by_arm["D"]["a2a_invokes"] == 1

    assert set(summary["overall"]) == {"A", "B", "C", "D"}
    assert {item["arm"] for item in summary["pairwise"]} == {"B", "C", "D"}
    assert summary["validity"]["mode"] == "fixture"
    assert summary["validity"]["persona_mode"] == "neutral"
    assert summary["run"]["persona_mode"] == "neutral"
    assert summary["run"]["task_count"] == 2
    assert len(summary["run"]["tasks_sha256"]) == 64
    assert any("authored answers" in warning for warning in summary["validity"]["warnings"])

    report = (output / "report.md").read_text(encoding="utf-8")
    assert "# Multi-Agent Evaluation Report" in report
    assert "## Takeaways" in report
    assert "## Paired comparisons against A" in report
    assert "## Key contrasts (B−A, C−B, D−C)" in report
    assert "## Paired contrasts by category" in report
    assert "## When is multi-agent worse?" in report
    assert "fixture mode replays authored answers" in report
    assert "sf-01" in report and "mc-01" in report


def test_runner_rejects_unknown_arm_and_missing_fixture(tmp_path):
    with pytest.raises(ValueError):
        EvalRunner(_config(tmp_path, arms=("A", "Z")))

    with pytest.raises(ValueError):
        EvalRunner(_config(tmp_path, fixture_path=tmp_path / "missing.json"))


def test_fixture_mode_can_resume_without_duplicating_trials(tmp_path):
    config = _config(tmp_path)
    EvalRunner(config).run()

    resumed = EvalRunner(_config(tmp_path, resume=True))
    resumed.run()

    lines = [
        json.loads(line)
        for line in (tmp_path / "eval" / "trials.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 8


def test_neutral_registry_keeps_ids_but_strips_persona_voice():
    original = AgentRegistry.builtin()
    neutral = _neutral_registry(original)

    assert neutral.ids() == original.ids()
    assert [agent.display_name for agent in neutral.all()] == ["P1", "P2", "P3"]
    assert all("只关注用户目标" in agent.persona for agent in neutral.all())
    assert all(not agent.trigger_points for agent in neutral.all())


def test_project_persona_mode_is_flagged_as_a_persona_experiment(tmp_path):
    summary = EvalRunner(
        _config(tmp_path, persona_mode="project", task_ids=["sf-01"], arms=("A", "B"))
    ).run()

    assert summary["validity"]["persona_mode"] == "project"
    assert any("persona experiment" in warning for warning in summary["validity"]["warnings"])


def test_runner_filters_tasks_and_categories(tmp_path):
    summary = EvalRunner(
        _config(tmp_path, task_ids=None, categories=["debugging"])
    ).run()

    assert summary["counts"]["tasks"] == 2
    assert set(summary["by_category"]) == {"debugging"}
