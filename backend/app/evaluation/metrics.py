"""Trace-derived metrics, paired comparisons and bootstrap intervals.

Everything here is computed from persisted mission messages/events plus the
metered adapter calls, so a trial can be audited after the fact.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from backend.app.domain.models import MessageRole, Mission, MissionEvent
from backend.app.evaluation.adapters import MeteredAdapter
from backend.app.evaluation.scoring import ScoreResult, score_answer
from backend.app.evaluation.tasks import EvalTask
from backend.app.storage.repository import Repository

_WHITESPACE = re.compile(r"\s+")
_WORD = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


@dataclass
class TrialResult:
    run_id: str
    task_id: str
    task_category: str
    task_title: str
    arm: str
    repeat: int
    mission_id: str
    status: str
    score: float
    passed: bool
    failed_check_ids: List[str]
    checks: List[dict]
    answer: str
    final_answer_chars: int
    any_message_score: float
    coverage_loss: float
    agent_messages: int
    turns_started: int
    turns_completed: int
    turns_blocked: int
    turns_failed: int
    a2a_invokes: int
    a2a_wait_reply: int
    a2a_skipped: int
    tool_calls: int
    tool_errors: int
    provider_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: Optional[float]
    token_source: str
    latency_ms: int
    wall_ms: int
    prompt_chars: int
    max_prompt_chars: int
    response_chars: int
    redundancy: float
    duplicate_messages: int
    started_at: str
    finished_at: str
    error: str = ""

    @property
    def key(self) -> Tuple[str, str, int]:
        return (self.task_id, self.arm, self.repeat)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "task_category": self.task_category,
            "task_title": self.task_title,
            "arm": self.arm,
            "repeat": self.repeat,
            "mission_id": self.mission_id,
            "status": self.status,
            "score": round(self.score, 4),
            "passed": self.passed,
            "failed_check_ids": list(self.failed_check_ids),
            "checks": list(self.checks),
            "answer": self.answer,
            "final_answer_chars": self.final_answer_chars,
            "any_message_score": round(self.any_message_score, 4),
            "coverage_loss": round(self.coverage_loss, 4),
            "agent_messages": self.agent_messages,
            "turns_started": self.turns_started,
            "turns_completed": self.turns_completed,
            "turns_blocked": self.turns_blocked,
            "turns_failed": self.turns_failed,
            "a2a_invokes": self.a2a_invokes,
            "a2a_wait_reply": self.a2a_wait_reply,
            "a2a_skipped": self.a2a_skipped,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "provider_calls": self.provider_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "token_source": self.token_source,
            "latency_ms": self.latency_ms,
            "wall_ms": self.wall_ms,
            "prompt_chars": self.prompt_chars,
            "max_prompt_chars": self.max_prompt_chars,
            "response_chars": self.response_chars,
            "redundancy": round(self.redundancy, 4),
            "duplicate_messages": self.duplicate_messages,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


def build_trial_result(
    *,
    run_id: str,
    task: EvalTask,
    arm: str,
    repeat: int,
    mission: Mission,
    repository: Repository,
    meter: MeteredAdapter,
    wall_ms: int,
    started_at: str,
    finished_at: str,
) -> TrialResult:
    messages = repository.list_messages(mission.id)
    agent_messages = [message for message in messages if message.role == MessageRole.AGENT]
    answer = agent_messages[-1].content if agent_messages else ""
    final_score: ScoreResult = score_answer(answer, task.rubric)
    any_score = max(
        (score_answer(message.content, task.rubric).score for message in agent_messages),
        default=0.0,
    )
    events = repository.list_events_after(mission.id, 0)

    counts = _count_events(events)
    usage_input, usage_output, usage_cost, usage_events = _usage(events)
    if usage_events:
        input_tokens = usage_input
        output_tokens = usage_output
        token_source = "usage"
    else:
        input_tokens = meter.estimated_input_tokens
        output_tokens = meter.estimated_output_tokens
        token_source = "estimate"

    mission_ok = mission.status in {"completed", "completed_with_warnings"}
    turn_ids = {
        str(event.payload.get("turn_id"))
        for event in events
        if event.event_type == "turn.started" and event.payload.get("turn_id")
    }
    turn_durations = [
        int(event.payload.get("duration_ms") or 0)
        for event in events
        if event.event_type in {"turn.completed", "turn.blocked", "turn.failed"}
    ]
    latency_ms = sum(turn_durations) if turn_durations else wall_ms
    response_chars = sum(len(message.content) for message in agent_messages)

    return TrialResult(
        run_id=run_id,
        task_id=task.id,
        task_category=task.category,
        task_title=task.title,
        arm=arm,
        repeat=repeat,
        mission_id=mission.id,
        status=str(mission.status),
        score=final_score.score,
        passed=final_score.passed and mission_ok,
        failed_check_ids=list(final_score.failed_check_ids),
        checks=[check.__dict__ for check in final_score.checks],
        answer=answer,
        final_answer_chars=len(answer),
        any_message_score=any_score,
        coverage_loss=max(0.0, any_score - final_score.score),
        agent_messages=len(agent_messages),
        turns_started=len(turn_ids) or counts["turns_started"],
        turns_completed=counts["turns_completed"],
        turns_blocked=counts["turns_blocked"],
        turns_failed=counts["turns_failed"],
        a2a_invokes=counts["a2a_invokes"],
        a2a_wait_reply=counts["a2a_wait_reply"],
        a2a_skipped=counts["a2a_skipped"],
        tool_calls=counts["tool_calls"],
        tool_errors=counts["tool_errors"],
        provider_calls=counts["turns_started"] or len(meter.calls),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cost_usd=usage_cost,
        token_source=token_source,
        latency_ms=latency_ms,
        wall_ms=wall_ms,
        prompt_chars=sum(call.prompt_chars for call in meter.calls),
        max_prompt_chars=meter.max_prompt_chars,
        response_chars=response_chars,
        redundancy=_redundancy([message.content for message in agent_messages]),
        duplicate_messages=_duplicate_messages([message.content for message in agent_messages]),
        started_at=started_at,
        finished_at=finished_at,
        error="" if mission.status in {"completed", "completed_with_warnings"} else _last_error(events),
    )


def _count_events(events: Iterable[MissionEvent]) -> Dict[str, int]:
    counts = {
        "turns_started": 0,
        "turns_completed": 0,
        "turns_blocked": 0,
        "turns_failed": 0,
        "a2a_invokes": 0,
        "a2a_wait_reply": 0,
        "a2a_skipped": 0,
        "tool_calls": 0,
        "tool_errors": 0,
    }
    for event in events:
        if event.event_type == "turn.started":
            counts["turns_started"] += 1
        elif event.event_type == "turn.completed":
            counts["turns_completed"] += 1
        elif event.event_type == "turn.blocked":
            counts["turns_blocked"] += 1
        elif event.event_type == "turn.failed":
            counts["turns_failed"] += 1
        elif event.event_type == "a2a.invoke":
            counts["a2a_invokes"] += 1
            if event.payload.get("mode") == "wait_reply":
                counts["a2a_wait_reply"] += 1
        elif event.event_type == "a2a.skipped":
            counts["a2a_skipped"] += 1
        elif event.event_type == "tool.call":
            counts["tool_calls"] += 1
        elif event.event_type == "tool.error":
            counts["tool_errors"] += 1
    return counts


def _usage(events: Iterable[MissionEvent]) -> Tuple[int, int, Optional[float], int]:
    input_tokens = 0
    output_tokens = 0
    cost_parts: List[float] = []
    count = 0
    for event in events:
        if event.event_type != "turn.usage":
            continue
        count += 1
        usage = event.payload.get("usage") or {}
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
        cost = usage.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            cost_parts.append(float(cost))
    return input_tokens, output_tokens, (sum(cost_parts) if cost_parts else None), count


def _last_error(events: Iterable[MissionEvent]) -> str:
    for event in reversed(list(events)):
        if event.event_type in {"turn.failed", "turn.blocked"}:
            return str(event.payload.get("error") or "")
        if event.event_type == "status" and event.payload.get("status") in {"failed", "blocked"}:
            return str(event.payload.get("summary") or "")
    return ""


def _redundancy(texts: Sequence[str]) -> float:
    usable = [text for text in texts if len(_WHITESPACE.sub("", text)) >= 80]
    if len(usable) < 2:
        return 0.0
    shingles = [_shingles(text) for text in usable]
    values: List[float] = []
    for i in range(len(shingles)):
        for j in range(i + 1, len(shingles)):
            left, right = shingles[i], shingles[j]
            if not left or not right:
                continue
            values.append(len(left & right) / len(left | right))
    return mean(values) if values else 0.0


def _shingles(text: str) -> set:
    compact = _WHITESPACE.sub("", text)
    return {compact[i : i + 3] for i in range(max(0, len(compact) - 2))}


def _duplicate_messages(texts: Sequence[str]) -> int:
    seen: Dict[str, int] = {}
    for text in texts:
        key = _WHITESPACE.sub(" ", text).strip()
        if len(key) < 20:
            continue
        seen[key] = seen.get(key, 0) + 1
    return sum(count - 1 for count in seen.values() if count > 1)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass
class ArmStats:
    arm: str
    n: int
    mean_score: float
    sd_score: float
    success_rate: float
    mean_total_tokens: float
    tokens_per_success: Optional[float]
    mean_input_tokens: float
    mean_output_tokens: float
    mean_provider_calls: float
    mean_latency_ms: float
    mean_turns: float
    mean_redundancy: float
    failure_rate: float
    mean_coverage_loss: float
    total_cost_usd: Optional[float]
    cost_per_success_usd: Optional[float]
    cost_coverage: float

    def to_dict(self) -> dict:
        return {
            "arm": self.arm,
            "n": self.n,
            "mean_score": round(self.mean_score, 4),
            "sd_score": round(self.sd_score, 4),
            "success_rate": round(self.success_rate, 4),
            "mean_total_tokens": round(self.mean_total_tokens, 1),
            "tokens_per_success": round(self.tokens_per_success, 1) if self.tokens_per_success is not None else None,
            "mean_input_tokens": round(self.mean_input_tokens, 1),
            "mean_output_tokens": round(self.mean_output_tokens, 1),
            "mean_provider_calls": round(self.mean_provider_calls, 2),
            "mean_latency_ms": round(self.mean_latency_ms, 1),
            "mean_turns": round(self.mean_turns, 2),
            "mean_redundancy": round(self.mean_redundancy, 4),
            "failure_rate": round(self.failure_rate, 4),
            "mean_coverage_loss": round(self.mean_coverage_loss, 4),
            "total_cost_usd": round(self.total_cost_usd, 6) if self.total_cost_usd is not None else None,
            "cost_per_success_usd": round(self.cost_per_success_usd, 6) if self.cost_per_success_usd is not None else None,
            "cost_coverage": round(self.cost_coverage, 3),
        }


def arm_stats(arm: str, trials: Sequence[TrialResult]) -> ArmStats:
    n = len(trials)
    if n == 0:
        return ArmStats(
            arm=arm,
            n=0,
            mean_score=0.0,
            sd_score=0.0,
            success_rate=0.0,
            mean_total_tokens=0.0,
            tokens_per_success=None,
            mean_input_tokens=0.0,
            mean_output_tokens=0.0,
            mean_provider_calls=0.0,
            mean_latency_ms=0.0,
            mean_turns=0.0,
            mean_redundancy=0.0,
            failure_rate=0.0,
            mean_coverage_loss=0.0,
            total_cost_usd=None,
            cost_per_success_usd=None,
            cost_coverage=0.0,
        )
    scores = [trial.score for trial in trials]
    costs = [trial.cost_usd for trial in trials if trial.cost_usd is not None]
    successes = sum(1 for trial in trials if trial.passed)
    total_tokens = sum(trial.total_tokens for trial in trials)
    total_cost = sum(costs) if costs else None
    return ArmStats(
        arm=arm,
        n=n,
        mean_score=mean(scores),
        sd_score=pstdev(scores) if n > 1 else 0.0,
        success_rate=successes / n,
        mean_total_tokens=mean([trial.total_tokens for trial in trials]),
        tokens_per_success=(total_tokens / successes) if successes else None,
        mean_input_tokens=mean([trial.input_tokens for trial in trials]),
        mean_output_tokens=mean([trial.output_tokens for trial in trials]),
        mean_provider_calls=mean([trial.provider_calls for trial in trials]),
        mean_latency_ms=mean([trial.latency_ms for trial in trials]),
        mean_turns=mean([trial.turns_started for trial in trials]),
        mean_redundancy=mean([trial.redundancy for trial in trials]),
        failure_rate=sum(
            1 for trial in trials if trial.status not in {"completed", "completed_with_warnings"}
        )
        / n,
        mean_coverage_loss=mean([trial.coverage_loss for trial in trials]),
        total_cost_usd=total_cost,
        cost_per_success_usd=(total_cost / successes) if total_cost is not None and successes else None,
        cost_coverage=len(costs) / n,
    )


def summarize(
    trials: Sequence[TrialResult],
    *,
    arms: Sequence[str],
    seed: int = 0,
    bootstrap_samples: int = 2000,
) -> dict:
    overall = {
        arm: arm_stats(arm, [trial for trial in trials if trial.arm == arm]).to_dict()
        for arm in arms
    }
    categories = sorted({trial.task_category for trial in trials})
    by_category: Dict[str, Dict[str, dict]] = {}
    for category in categories:
        subset = [trial for trial in trials if trial.task_category == category]
        by_category[category] = {
            arm: arm_stats(arm, [trial for trial in subset if trial.arm == arm]).to_dict()
            for arm in arms
            if any(trial.arm == arm for trial in subset)
        }

    pairwise = _pairwise(
        trials,
        arms,
        baseline_arm="A",
        candidates=[arm for arm in arms if arm != "A"],
        seed=seed,
        bootstrap_samples=bootstrap_samples,
    )
    contrasts = _contrasts(trials, arms, seed=seed, bootstrap_samples=bootstrap_samples)
    pairwise_by_category = []
    contrasts_by_category = []
    for category in categories:
        subset = [trial for trial in trials if trial.task_category == category]
        for comparison in _pairwise(
            subset,
            arms,
            baseline_arm="A",
            candidates=[arm for arm in arms if arm != "A"],
            seed=seed,
            bootstrap_samples=bootstrap_samples,
        ):
            comparison["category"] = category
            pairwise_by_category.append(comparison)
        for comparison in _contrasts(
            subset, arms, seed=seed, bootstrap_samples=bootstrap_samples
        ):
            comparison["category"] = category
            contrasts_by_category.append(comparison)

    degradation = _degradation(trials, arms, by_category)
    pareto = _pareto(overall, arms)

    return {
        "overall": overall,
        "by_category": by_category,
        "pairwise": pairwise,
        "pairwise_by_category": pairwise_by_category,
        "contrasts": contrasts,
        "contrasts_by_category": contrasts_by_category,
        "degradation": degradation,
        "pareto": pareto,
        "counts": {
            "trials": len(trials),
            "tasks": len({trial.task_id for trial in trials}),
            "arms": list(arms),
            "categories": categories,
        },
    }


_CONTRASTS: Tuple[Tuple[str, str], ...] = (("A", "B"), ("B", "C"), ("C", "D"))


def _contrasts(
    trials: Sequence[TrialResult],
    arms: Sequence[str],
    *,
    seed: int,
    bootstrap_samples: int,
) -> List[dict]:
    comparisons: List[dict] = []
    for baseline, arm in _CONTRASTS:
        comparisons.extend(
            _pairwise(
                trials,
                arms,
                baseline_arm=baseline,
                candidates=[arm],
                seed=seed,
                bootstrap_samples=bootstrap_samples,
            )
        )
    return comparisons


def _pairwise(
    trials: Sequence[TrialResult],
    arms: Sequence[str],
    *,
    baseline_arm: str = "A",
    candidates: Optional[Sequence[str]] = None,
    seed: int,
    bootstrap_samples: int,
) -> List[dict]:
    if baseline_arm not in arms:
        return []
    by_arm: Dict[str, Dict[Tuple[str, int], TrialResult]] = {arm: {} for arm in arms}
    for trial in trials:
        by_arm.setdefault(trial.arm, {})[(trial.task_id, trial.repeat)] = trial
    baseline = by_arm.get(baseline_arm, {})
    comparisons: List[dict] = []
    for arm in (candidates if candidates is not None else [a for a in arms if a != baseline_arm]):
        if arm == baseline_arm:
            continue
        current = by_arm.get(arm, {})
        keys = sorted(set(baseline) & set(current))
        deltas = [current[key].score - baseline[key].score for key in keys]
        if not deltas:
            continue
        wins = sum(1 for delta in deltas if delta > 1e-9)
        losses = sum(1 for delta in deltas if delta < -1e-9)
        ties = len(deltas) - wins - losses
        ci_low, ci_high = bootstrap_mean_ci(
            deltas, seed=seed, samples=bootstrap_samples
        )
        baseline_mean = mean([baseline[key].score for key in keys])
        relative = mean(deltas) / baseline_mean if baseline_mean else None
        comparisons.append(
            {
                "baseline": baseline_arm,
                "arm": arm,
                "n": len(keys),
                "mean_delta": round(mean(deltas), 4),
                "relative_delta": round(relative, 4) if relative is not None else None,
                "ci_low": round(ci_low, 4),
                "ci_high": round(ci_high, 4),
                "wins": wins,
                "ties": ties,
                "losses": losses,
                "sign_test_p": round(sign_test_p(wins, losses), 4),
                "baseline_mean_score": round(baseline_mean, 4),
                "arm_mean_score": round(mean([current[key].score for key in keys]), 4),
            }
        )
    return comparisons


def _degradation(
    trials: Sequence[TrialResult],
    arms: Sequence[str],
    by_category: Dict[str, Dict[str, dict]],
) -> List[dict]:
    flags: List[dict] = []
    if "A" not in arms:
        return flags

    per_task: Dict[str, Dict[str, List[TrialResult]]] = {}
    for trial in trials:
        per_task.setdefault(trial.task_id, {}).setdefault(trial.arm, []).append(trial)

    for task_id, by_arm in per_task.items():
        if "A" not in by_arm:
            continue
        baseline = mean([trial.score for trial in by_arm["A"]])
        category = by_arm["A"][0].task_category
        for arm in arms:
            if arm == "A" or arm not in by_arm:
                continue
            current = mean([trial.score for trial in by_arm[arm]])
            if current + 0.1 <= baseline + 1e-9:
                flags.append(
                    {
                        "scope": "task",
                        "task_id": task_id,
                        "category": category,
                        "arm": arm,
                        "baseline_score": round(baseline, 4),
                        "arm_score": round(current, 4),
                        "delta": round(current - baseline, 4),
                        "reason": "quality_regression",
                    }
                )

    for category, stats in by_category.items():
        if "A" not in stats:
            continue
        baseline = stats["A"]
        for arm in arms:
            if arm == "A" or arm not in stats:
                continue
            current = stats[arm]
            delta = current["mean_score"] - baseline["mean_score"]
            if delta <= -0.05:
                flags.append(
                    {
                        "scope": "category",
                        "category": category,
                        "arm": arm,
                        "baseline_score": baseline["mean_score"],
                        "arm_score": current["mean_score"],
                        "delta": round(delta, 4),
                        "reason": "quality_regression",
                    }
                )
            if (
                baseline["mean_total_tokens"] > 0
                and current["mean_total_tokens"] >= 2 * baseline["mean_total_tokens"]
                and delta <= 0.02
            ):
                flags.append(
                    {
                        "scope": "category",
                        "category": category,
                        "arm": arm,
                        "token_ratio": round(
                            current["mean_total_tokens"] / baseline["mean_total_tokens"], 2
                        ),
                        "delta": round(delta, 4),
                        "reason": "coordination_tax",
                    }
                )
    return flags


def _pareto(overall: Dict[str, dict], arms: Sequence[str]) -> List[dict]:
    points = []
    for arm in arms:
        stats = overall.get(arm)
        if not stats or stats["n"] == 0:
            continue
        points.append(
            {
                "arm": arm,
                "mean_score": stats["mean_score"],
                "mean_total_tokens": stats["mean_total_tokens"],
                "mean_latency_ms": stats["mean_latency_ms"],
            }
        )
    frontier = []
    for point in points:
        dominated_by = []
        for other in points:
            if other is point:
                continue
            no_worse = (
                other["mean_score"] >= point["mean_score"]
                and other["mean_total_tokens"] <= point["mean_total_tokens"]
            )
            strictly_better = (
                other["mean_score"] > point["mean_score"]
                or other["mean_total_tokens"] < point["mean_total_tokens"]
            )
            if no_worse and strictly_better:
                dominated_by.append(other["arm"])
        frontier.append({**point, "dominated_by": dominated_by, "pareto_optimal": not dominated_by})
    return frontier


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    seed: int = 0,
    samples: int = 2000,
    confidence: float = 0.95,
) -> Tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (values[0], values[0])
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(max(100, samples)):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    alpha = (1.0 - confidence) / 2.0
    low = means[int(alpha * (len(means) - 1))]
    high = means[int((1.0 - alpha) * (len(means) - 1))]
    return (low, high)


def sign_test_p(wins: int, losses: int) -> float:
    """Two-sided exact binomial sign test; ties are excluded by the caller."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)
