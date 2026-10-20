"""Deterministic rubric scoring for evaluation answers.

Checks are deliberately simple and inspectable: a human can see exactly which
requirement failed.  They are not a replacement for human review, but they make
single-agent vs multi-agent comparisons reproducible and cheap.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from backend.app.evaluation.tasks import Rubric, RubricCheck

_WHITESPACE = re.compile(r"\s+")
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class CheckOutcome:
    check_id: str
    kind: str
    weight: float
    passed: bool
    description: str = ""
    evidence: str = ""


@dataclass(frozen=True)
class ScoreResult:
    score: float
    passed: bool
    checks: List[CheckOutcome] = field(default_factory=list)
    failed_check_ids: List[str] = field(default_factory=list)
    answer_chars: int = 0

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "passed": self.passed,
            "answer_chars": self.answer_chars,
            "failed_check_ids": list(self.failed_check_ids),
            "checks": [
                {
                    "check_id": check.check_id,
                    "kind": check.kind,
                    "weight": check.weight,
                    "passed": check.passed,
                    "description": check.description,
                    "evidence": check.evidence,
                }
                for check in self.checks
            ],
        }


def score_answer(answer: str, rubric: Rubric) -> ScoreResult:
    text = (answer or "").strip()
    outcomes: List[CheckOutcome] = []
    earned = 0.0
    total = rubric.total_weight

    for check in rubric.checks:
        passed, evidence = _evaluate(check, text)
        if passed:
            earned += check.weight
        outcomes.append(
            CheckOutcome(
                check_id=check.id,
                kind=check.kind,
                weight=check.weight,
                passed=passed,
                description=check.description,
                evidence=evidence,
            )
        )

    score = earned / total if total else 0.0
    failed = [outcome.check_id for outcome in outcomes if not outcome.passed]
    return ScoreResult(
        score=score,
        passed=score + 1e-9 >= rubric.threshold,
        checks=outcomes,
        failed_check_ids=failed,
        answer_chars=len(text),
    )


def _evaluate(check: RubricCheck, answer: str) -> tuple[bool, str]:
    normalized_answer = _normalize(answer, check.case_sensitive)
    evidence = answer[:160].replace("\n", " ")
    if check.kind == "contains_all":
        missing = [
            value
            for value in check.values
            if _normalize(value, check.case_sensitive) not in normalized_answer
        ]
        return not missing, ("missing: " + ", ".join(missing)) if missing else evidence
    if check.kind == "contains_any":
        present = [
            value
            for value in check.values
            if _normalize(value, check.case_sensitive) in normalized_answer
        ]
        return bool(present), ("matched: " + ", ".join(present)) if present else evidence
    if check.kind == "not_contains":
        present = [
            value
            for value in check.values
            if _normalize(value, check.case_sensitive) in normalized_answer
        ]
        return not present, ("forbidden: " + ", ".join(present)) if present else evidence
    if check.kind == "regex":
        return _regex(check, answer)
    if check.kind == "not_regex":
        passed, evidence = _regex(check, answer)
        return (not passed), ("unexpected match: " + evidence) if passed else "no forbidden match"
    if check.kind == "min_matches":
        try:
            matches = re.findall(
                str(check.value), answer, 0 if check.case_sensitive else re.IGNORECASE
            )
        except re.error as exc:
            return False, "invalid regex: {error}".format(error=exc)
        return len(matches) >= int(check.count or 1), "matches: {n}".format(n=len(matches))
    if check.kind == "max_chars":
        return len(answer) <= int(check.value), "chars: {n}".format(n=len(answer))
    if check.kind == "min_chars":
        return len(answer) >= int(check.value), "chars: {n}".format(n=len(answer))
    if check.kind == "json_fields":
        return _json_fields(check, answer)
    if check.kind == "json_exact":
        return _json_exact(check, answer)
    if check.kind == "equals":
        return normalized_answer == _normalize(str(check.value), check.case_sensitive), evidence
    return False, "unsupported check kind: {kind}".format(kind=check.kind)


def _regex(check: RubricCheck, answer: str) -> tuple[bool, str]:
    try:
        match = re.search(
            str(check.value), answer, 0 if check.case_sensitive else re.IGNORECASE
        )
    except re.error as exc:
        return False, "invalid regex: {error}".format(error=exc)
    return bool(match), ("matched: " + match.group(0)[:80]) if match else answer[:160]


def _json_fields(check: RubricCheck, answer: str) -> tuple[bool, str]:
    payload = _extract_json(answer)
    if payload is None:
        return False, "no JSON object found"
    expected = check.value if isinstance(check.value, dict) else {}
    missing_or_wrong = []
    for key, want in expected.items():
        if key not in payload:
            missing_or_wrong.append("{key}=<missing>".format(key=key))
            continue
        actual = payload[key]
        if want == "*":
            if actual in (None, "", [], {}):
                missing_or_wrong.append("{key}=<empty>".format(key=key))
            continue
        if not check.case_sensitive:
            if str(actual).strip().lower() != str(want).strip().lower():
                missing_or_wrong.append("{key}={actual!r}".format(key=key, actual=actual))
        elif str(actual).strip() != str(want).strip():
            missing_or_wrong.append("{key}={actual!r}".format(key=key, actual=actual))
    return not missing_or_wrong, (
        "bad fields: " + ", ".join(missing_or_wrong) if missing_or_wrong else "JSON fields ok"
    )


def _json_exact(check: RubricCheck, answer: str) -> tuple[bool, str]:
    """Require one complete JSON object, preserving types and array order."""
    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate JSON key")
            obj[key] = value
        return obj

    def reject_constant(value):
        raise ValueError("non-finite JSON number: " + value)

    try:
        actual = json.loads(
            answer, object_pairs_hook=unique_object, parse_constant=reject_constant,
        )
        if not isinstance(actual, dict):
            return False, "expected one JSON object"
        canonical = dict(ensure_ascii=False, sort_keys=True, allow_nan=False)
        passed = json.dumps(actual, **canonical) == json.dumps(check.value, **canonical)
    except (ValueError, TypeError) as exc:
        return False, "invalid strict JSON: " + str(exc)[:120]
    return passed, "exact JSON match" if passed else "JSON value, type or key mismatch"


def _extract_json(answer: str) -> Optional[dict]:
    candidates: List[str] = []
    fence = _JSON_FENCE.search(answer)
    if fence:
        candidates.append(fence.group(1))
    start = answer.find("{")
    end = answer.rfind("}")
    if start != -1 and end > start:
        candidates.append(answer[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _normalize(text: str, case_sensitive: bool) -> str:
    normalized = _WHITESPACE.sub(" ", text or "").strip()
    return normalized if case_sensitive else normalized.lower()
