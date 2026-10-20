import pytest

from backend.app.evaluation.scoring import score_answer
from backend.app.evaluation.tasks import Rubric, RubricCheck


def _rubric(*checks, threshold=0.8):
    return Rubric(threshold=threshold, checks=list(checks))


def test_all_check_kinds_can_pass():
    rubric = _rubric(
        RubricCheck(id="all", kind="contains_all", values=["Alpha", "Beta"], weight=1),
        RubricCheck(id="any", kind="contains_any", values=["nope", "Beta"], weight=1),
        RubricCheck(id="not", kind="not_contains", values=["forbidden"], weight=1),
        RubricCheck(id="regex", kind="regex", value=r"\b42\b", weight=1),
        RubricCheck(id="matches", kind="min_matches", value="[?？]", count=2, weight=1),
        RubricCheck(id="max", kind="max_chars", value=200, weight=1),
        RubricCheck(id="min", kind="min_chars", value=10, weight=1),
        RubricCheck(
            id="json",
            kind="json_fields",
            value={"status": "ok", "count": 3, "name": "*"},
            weight=1,
        ),
    )
    answer = 'alpha beta 42?? {"status": "ok", "count": 3, "name": "demo"}'

    result = score_answer(answer, rubric)

    assert result.passed
    assert result.score == 1.0
    assert result.failed_check_ids == []


def test_equals_check_normalizes_whitespace_and_case():
    rubric = _rubric(RubricCheck(id="equals", kind="equals", value="alpha   beta"))

    assert score_answer("  ALPHA beta ", rubric).passed
    assert not score_answer("alpha beta extra", rubric).passed


def test_failed_checks_are_reported_with_weights():
    rubric = _rubric(
        RubricCheck(id="all", kind="contains_all", values=["Alpha", "Beta"], weight=2),
        RubricCheck(id="any", kind="contains_any", values=["Gamma"], weight=1),
        RubricCheck(id="not", kind="not_contains", values=["forbidden"], weight=3),
    )

    result = score_answer("alpha only", rubric)

    assert not result.passed
    assert result.score == pytest.approx(3 / 6)
    assert set(result.failed_check_ids) == {"all", "any"}
    assert {check.check_id: check.passed for check in result.checks} == {
        "all": False,
        "any": False,
        "not": True,
    }


def test_contains_checks_are_case_insensitive_by_default():
    rubric = _rubric(RubricCheck(id="all", kind="contains_all", values=["hello"]))

    assert score_answer("HELLO", rubric).passed
    sensitive = _rubric(
        RubricCheck(id="all", kind="contains_all", values=["hello"], case_sensitive=True)
    )
    assert not score_answer("HELLO", sensitive).passed


def test_json_fields_accepts_fenced_json_and_reports_missing_keys():
    rubric = _rubric(
        RubricCheck(
            id="json",
            kind="json_fields",
            value={"code": "PASS", "note": "*"},
        )
    )

    ok = score_answer('```json\n{"code": "pass", "note": "fine"}\n```', rubric)
    assert ok.passed

    missing = score_answer('```json\n{"code": "pass"}\n```', rubric)
    assert not missing.passed
    assert "note" in missing.checks[0].evidence


def test_not_regex_allows_negated_mentions_but_rejects_recommendations():
    rubric = _rubric(
        RubricCheck(
            id="no_rewrite",
            kind="not_regex",
            value=r"(?<!不)(建议|应该|需要|可以)\s*完全重写",
        )
    )

    assert score_answer("不需要完全重写，只改这一行。", rubric).passed
    rejected = score_answer("建议完全重写这个函数。", rubric)
    assert not rejected.passed
    assert "unexpected match" in rejected.checks[0].evidence


def test_invalid_regex_fails_without_raising():
    rubric = _rubric(RubricCheck(id="bad", kind="regex", value="[unclosed"))

    result = score_answer("anything", rubric)

    assert not result.passed
    assert "invalid regex" in result.checks[0].evidence


def test_threshold_controls_pass_flag_but_not_score():
    rubric = _rubric(
        RubricCheck(id="a", kind="contains_any", values=["yes"], weight=1),
        RubricCheck(id="b", kind="contains_any", values=["no"], weight=1),
        threshold=0.8,
    )

    result = score_answer("yes", rubric)

    assert result.score == 0.5
    assert not result.passed
    assert result.to_dict()["score"] == 0.5
