"""Markdown report rendering for evaluation runs."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from backend.app.evaluation.metrics import TrialResult


def render_report(trials: Sequence[TrialResult], summary: dict) -> str:
    run = summary.get("run", {})
    validity = summary.get("validity", {})
    overall = summary.get("overall", {})
    pairwise = summary.get("pairwise", [])
    by_category = summary.get("by_category", {})
    degradation = summary.get("degradation", [])
    pareto = summary.get("pareto", [])
    arms = [arm for arm in run.get("arms", []) if arm in overall]
    labels = run.get("arm_labels", {})

    lines: List[str] = []
    lines.append("# Multi-Agent Evaluation Report")
    lines.append("")
    lines.append("Run: `{run_id}`  ".format(run_id=run.get("run_id", "?")))
    lines.append("Mode: `{mode}`  Provider: `{provider}`  Lead: `{lead}`  Persona: `{persona}`  Repeats: `{repeats}`  Seed: `{seed}`".format(
        mode=run.get("mode", "?"),
        provider=run.get("provider") or "configured-defaults",
        lead=run.get("lead_agent_id", "?"),
        persona=run.get("persona_mode", "?"),
        repeats=run.get("repeats", "?"),
        seed=run.get("seed", "?"),
    ))
    lines.append("")

    warnings = validity.get("warnings") or []
    if warnings:
        lines.append("> **Validity warnings**")
        for warning in warnings:
            lines.append("> - {warning}".format(warning=warning))
        lines.append("")

    lines.append("## Arms")
    lines.append("")
    lines.append("| Arm | Strategy |")
    lines.append("| --- | --- |")
    for arm in arms:
        lines.append(
            "| {arm} | {label} — {description} |".format(
                arm=arm,
                label=labels.get(arm, arm),
                description=run.get("arm_descriptions", {}).get(arm, ""),
            )
        )
    lines.append("")

    lines.append("## Headline")
    lines.append("")
    lines.append(
        "| Arm | n | Mean score | SD | Success | Tokens | Tokens/success | Provider calls | Latency (ms) | Turns | Redundancy | Failure |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for arm in arms:
        stats = overall.get(arm)
        if not stats:
            continue
        lines.append(
            "| {arm} | {n} | {score} | {sd} | {success} | {tokens} | {tokens_per_success} | {calls} | {latency} | {turns} | {redundancy} | {failure} |".format(
                arm=arm,
                n=stats["n"],
                score=_num(stats["mean_score"], 3),
                sd=_num(stats["sd_score"], 3),
                success=_pct(stats["success_rate"]),
                tokens=_num(stats["mean_total_tokens"], 0),
                tokens_per_success=_num(stats.get("tokens_per_success"), 0),
                calls=_num(stats["mean_provider_calls"], 2),
                latency=_num(stats["mean_latency_ms"], 0),
                turns=_num(stats["mean_turns"], 2),
                redundancy=_num(stats["mean_redundancy"], 3),
                failure=_pct(stats["failure_rate"]),
            )
        )
    lines.append("")

    lines.append("## Takeaways")
    lines.append("")
    if overall:
        best_arm = max(arms, key=lambda arm: overall[arm]["mean_score"])
        lines.append(
            "- Highest mean score: **{arm}** ({score}).".format(
                arm=best_arm, score=_num(overall[best_arm]["mean_score"], 3)
            )
        )
        efficient = [
            arm
            for arm in arms
            if overall.get(arm, {}).get("tokens_per_success") is not None
        ]
        if efficient:
            cheapest = min(efficient, key=lambda arm: overall[arm]["tokens_per_success"])
            lines.append(
                "- Lowest tokens per success: **{arm}** ({tokens}).".format(
                    arm=cheapest,
                    tokens=_num(overall[cheapest]["tokens_per_success"], 0),
                )
            )
    contrasts_by_pair = {
        (item["baseline"], item["arm"]): item
        for item in summary.get("contrasts", [])
    }
    for baseline, arm, label in (
        ("A", "B", "Extra checking"),
        ("B", "C", "Identity switch"),
        ("C", "D", "Dynamic routing"),
    ):
        item = contrasts_by_pair.get((baseline, arm))
        if item:
            lines.append(
                "- {label} ({arm}−{baseline}): Δ={delta} (95% CI [{low}, {high}]).".format(
                    label=label,
                    arm=arm,
                    baseline=baseline,
                    delta=_signed(item["mean_delta"]),
                    low=_signed(item["ci_low"]),
                    high=_signed(item["ci_high"]),
                )
            )
    regressions = [
        flag
        for flag in degradation
        if flag.get("reason") == "quality_regression"
        and flag.get("scope") == "category"
    ]
    if regressions:
        lines.append(
            "- Category-level regressions flagged: {items}.".format(
                items=", ".join(
                    "{category}/{arm}".format(category=flag["category"], arm=flag["arm"])
                    for flag in regressions
                )
            )
        )
    lines.append("")

    lines.append("## Paired comparisons against A (Single Agent)")
    lines.append("")
    if not pairwise:
        lines.append("No paired comparison available (arm A missing or no shared task/repeat).")
    else:
        lines.append(
            "| Arm | n pairs | Δ score | 95% CI | Relative | Wins | Ties | Losses | Sign-test p |"
        )
        lines.append("| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |")
        for comparison in pairwise:
            lines.append(
                "| {arm} | {n} | {delta} | [{low}, {high}] | {relative} | {wins} | {ties} | {losses} | {p} |".format(
                    arm=comparison["arm"],
                    n=comparison["n"],
                    delta=_signed(comparison["mean_delta"]),
                    low=_signed(comparison["ci_low"]),
                    high=_signed(comparison["ci_high"]),
                    relative=_pct(comparison.get("relative_delta"), signed=True),
                    wins=comparison["wins"],
                    ties=comparison["ties"],
                    losses=comparison["losses"],
                    p=_num(comparison["sign_test_p"], 4),
                )
            )
        lines.append("")
        lines.append("Positive Δ means the arm scored higher than A on the same task/repeat.")
    lines.append("")

    contrasts = summary.get("contrasts", [])
    if contrasts:
        lines.append("## Key contrasts (B−A, C−B, D−C)")
        lines.append("")
        lines.append(
            "These are the pre-declared comparisons: B−A isolates extra checking; "
            "C−B isolates execution identity at the same 4-turn skeleton; D−C isolates dynamic routing."
        )
        lines.append("")
        lines.append("| Contrast | n | Δ score | 95% CI | Wins | Ties | Losses | Sign-test p |")
        lines.append("| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |")
        for comparison in contrasts:
            lines.append(
                "| {label} | {n} | {delta} | [{low}, {high}] | {wins} | {ties} | {losses} | {p} |".format(
                    label=_contrast_label(comparison["baseline"], comparison["arm"]),
                    n=comparison["n"],
                    delta=_signed(comparison["mean_delta"]),
                    low=_signed(comparison["ci_low"]),
                    high=_signed(comparison["ci_high"]),
                    wins=comparison["wins"],
                    ties=comparison["ties"],
                    losses=comparison["losses"],
                    p=_num(comparison["sign_test_p"], 4),
                )
            )
        lines.append("")

    lines.append("## Score by task category")
    lines.append("")
    categories = sorted(by_category)
    if categories:
        header = "| Category | " + " | ".join("{arm} n / score".format(arm=arm) for arm in arms) + " | Best |"
        lines.append(header)
        lines.append("| --- | " + " | ".join("---:" for _ in arms) + " | --- |")
        for category in categories:
            stats = by_category[category]
            cells = []
            best_arms = []
            best_score = -1.0
            for arm in arms:
                item = stats.get(arm)
                if not item:
                    cells.append("—")
                    continue
                cells.append("{n} / {score}".format(n=item["n"], score=_num(item["mean_score"], 3)))
                if item["mean_score"] > best_score + 1e-9:
                    best_score = item["mean_score"]
                    best_arms = [arm]
                elif abs(item["mean_score"] - best_score) <= 1e-9:
                    best_arms.append(arm)
            lines.append(
                "| {category} | {cells} | {best} |".format(
                    category=category,
                    cells=" | ".join(cells),
                    best="/".join(best_arms) or "—",
                )
            )
    else:
        lines.append("No category data.")
    lines.append("")

    contrast_rows: Dict[str, Dict[tuple, dict]] = {}
    for item in summary.get("contrasts_by_category", []):
        contrast_rows.setdefault(item["category"], {})[
            (item["baseline"], item["arm"])
        ] = item
    if contrast_rows:
        lines.append("## Paired contrasts by category")
        lines.append("")
        lines.append("| Category | B−A | C−B | D−C |")
        lines.append("| --- | ---: | ---: | ---: |")
        for category in categories:
            cells = []
            for pair in (("A", "B"), ("B", "C"), ("C", "D")):
                item = contrast_rows.get(category, {}).get(pair)
                if not item:
                    cells.append("—")
                else:
                    cells.append(
                        "{delta} ({wins}/{ties}/{losses})".format(
                            delta=_signed(item["mean_delta"]),
                            wins=item["wins"],
                            ties=item["ties"],
                            losses=item["losses"],
                        )
                    )
            lines.append("| {category} | {cells} |".format(category=category, cells=" | ".join(cells)))
        lines.append("")

    lines.append("## Coordination cost by category (mean tokens)")
    lines.append("")
    if categories:
        lines.append("| Category | " + " | ".join(arms) + " |")
        lines.append("| --- | " + " | ".join("---:" for _ in arms) + " |")
        for category in categories:
            stats = by_category[category]
            cells = [
                _num(stats[arm]["mean_total_tokens"], 0) if arm in stats else "—"
                for arm in arms
            ]
            lines.append("| {category} | {cells} |".format(category=category, cells=" | ".join(cells)))
    lines.append("")

    lines.append("## Score by task")
    lines.append("")
    tasks = _task_rows(trials, arms)
    if tasks:
        lines.append("| Task | Category | " + " | ".join(arms) + " | D−A |")
        lines.append("| --- | --- | " + " | ".join("---:" for _ in arms) + " | ---: |")
        for row in tasks:
            delta = row["scores"].get("D")
            baseline = row["scores"].get("A")
            delta_text = (
                _signed(delta - baseline)
                if delta is not None and baseline is not None
                else "—"
            )
            lines.append(
                "| {task} | {category} | {scores} | {delta} |".format(
                    task=row["task_id"],
                    category=row["category"],
                    scores=" | ".join(
                        _num(row["scores"][arm], 3) if arm in row["scores"] else "—"
                        for arm in arms
                    ),
                    delta=delta_text,
                )
            )
    lines.append("")

    lines.append("## When is multi-agent worse?")
    lines.append("")
    quality_flags = [
        flag
        for flag in degradation
        if flag.get("scope") == "category" and flag.get("reason") == "quality_regression"
    ]
    task_flags = [
        flag
        for flag in degradation
        if flag.get("scope") == "task" and flag.get("reason") == "quality_regression"
    ]
    cost_flags = [flag for flag in degradation if flag.get("reason") == "coordination_tax"]
    if not quality_flags and not task_flags:
        lines.append(
            "No category- or task-level quality regression was flagged at the current "
            "thresholds (category Δ ≤ −0.05, task Δ ≤ −0.10)."
        )
    else:
        if quality_flags:
            lines.append("**Category-level regressions** (multi-agent arm scores below A):")
            lines.append("")
            lines.append("| Category | Arm | A score | Arm score | Δ |")
            lines.append("| --- | --- | ---: | ---: | ---: |")
            for flag in quality_flags:
                lines.append(
                    "| {category} | {arm} | {base} | {score} | {delta} |".format(
                        category=flag["category"],
                        arm=flag["arm"],
                        base=_num(flag["baseline_score"], 3),
                        score=_num(flag["arm_score"], 3),
                        delta=_signed(flag["delta"]),
                    )
                )
            lines.append("")
        if task_flags:
            lines.append("**Task-level regressions** (at least 0.10 below A):")
            lines.append("")
            lines.append("| Task | Category | Arm | A score | Arm score | Δ |")
            lines.append("| --- | --- | --- | ---: | ---: | ---: |")
            for flag in task_flags[:20]:
                lines.append(
                    "| {task} | {category} | {arm} | {base} | {score} | {delta} |".format(
                        task=flag["task_id"],
                        category=flag["category"],
                        arm=flag["arm"],
                        base=_num(flag["baseline_score"], 3),
                        score=_num(flag["arm_score"], 3),
                        delta=_signed(flag["delta"]),
                    )
                )
            lines.append("")
    if cost_flags:
        lines.append("**Overhead without quality gain** (≥2× A tokens and no quality gain):")
        lines.append("")
        lines.append("| Category | Arm | Token ratio | Δ score |")
        lines.append("| --- | --- | ---: | ---: |")
        for flag in cost_flags:
            lines.append(
                "| {category} | {arm} | {ratio}× | {delta} |".format(
                    category=flag["category"],
                    arm=flag["arm"],
                    ratio=_num(flag["token_ratio"], 2),
                    delta=_signed(flag["delta"]),
                )
            )
        lines.append("")

    lines.append("## Cost–quality frontier")
    lines.append("")
    if pareto:
        lines.append("| Arm | Mean score | Mean tokens | Dominated by | Pareto optimal |")
        lines.append("| --- | ---: | ---: | --- | --- |")
        for point in pareto:
            lines.append(
                "| {arm} | {score} | {tokens} | {dominated} | {optimal} |".format(
                    arm=point["arm"],
                    score=_num(point["mean_score"], 3),
                    tokens=_num(point["mean_total_tokens"], 0),
                    dominated=", ".join(point["dominated_by"]) or "—",
                    optimal="yes" if point["pareto_optimal"] else "no",
                )
            )
    lines.append("")

    coverage_losses = [
        (trial.task_id, trial.arm, trial.coverage_loss)
        for trial in trials
        if trial.coverage_loss >= 0.1
    ]
    if coverage_losses:
        lines.append("## Intermediate-message coverage loss")
        lines.append("")
        lines.append(
            "The final message scored lower than the best intermediate agent message. "
            "This is a summary/selection loss signal, not a quality win for the arm."
        )
        lines.append("")
        lines.append("| Task | Arm | Coverage loss |")
        lines.append("| --- | --- | ---: |")
        for task_id, arm, loss in coverage_losses[:15]:
            lines.append("| {task} | {arm} | {loss} |".format(task=task_id, arm=arm, loss=_num(loss, 3)))
        lines.append("")

    lines.append("## Method")
    lines.append("")
    lines.append("- Every task/arm/repeat gets a fresh conversation, mission and adapter; no cross-trial context.")
    lines.append("- The lead agent is identical across arms; in real mode pin all agents to one provider with `--provider`.")
    lines.append("- Persona mode is `neutral` by default: IDs stay stable, but display names and persona instructions are stripped so orchestration is the variable under test.")
    lines.append("- Arms are paired within task/repeat. Δ is computed per pair, then averaged; the 95% interval is a percentile bootstrap over pairs.")
    lines.append("- `B` and `C` share the same 4-turn skeleton (draft, check one, check two, revise); `C` changes execution identity, `D` adds up to 2 dynamic A2A turns on the same skeleton.")
    lines.append("- The pre-declared contrasts are B−A, C−B and D−C. The A-baseline table is kept for degradation analysis.")
    lines.append("- Scores come from the task rubric (`eval/tasks.json`). Tokens use provider `turn.usage` when present, otherwise a `chars/3` estimate.")
    lines.append("- Task set SHA-256: `{task_hash}`; fixture SHA-256: `{fixture_hash}`.".format(
        task_hash=run.get("tasks_sha256", "?"),
        fixture_hash=run.get("fixture_sha256") or "—",
    ))
    lines.append("- Redundancy is mean pairwise Jaccard similarity of 3-character shingles across agent messages (≥80 chars).")
    lines.append("")

    failed = [trial for trial in trials if trial.status not in {"completed", "completed_with_warnings"}]
    if failed:
        lines.append("## Failed or blocked trials")
        lines.append("")
        lines.append("| Task | Arm | Repeat | Status | Error |")
        lines.append("| --- | --- | ---: | --- | --- |")
        for trial in failed[:20]:
            lines.append(
                "| {task} | {arm} | {repeat} | {status} | {error} |".format(
                    task=trial.task_id,
                    arm=trial.arm,
                    repeat=trial.repeat,
                    status=trial.status,
                    error=(trial.error or "").replace("|", "/")[:160],
                )
            )
        lines.append("")

    lines.append("## Appendix: trials")
    lines.append("")
    lines.append("| Task | Arm | Rep | Status | Score | Tokens | Calls | A2A | Failed checks |")
    lines.append("| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |")
    for trial in sorted(trials, key=lambda item: (item.task_id, item.arm, item.repeat)):
        lines.append(
            "| {task} | {arm} | {repeat} | {status} | {score} | {tokens} | {calls} | {a2a} | {failed} |".format(
                task=trial.task_id,
                arm=trial.arm,
                repeat=trial.repeat,
                status=trial.status,
                score=_num(trial.score, 3),
                tokens=trial.total_tokens,
                calls=trial.provider_calls,
                a2a=trial.a2a_invokes,
                failed=", ".join(trial.failed_check_ids) or "—",
            )
        )
    lines.append("")
    return "\n".join(lines)


def _task_rows(trials: Sequence[TrialResult], arms: Sequence[str]) -> List[dict]:
    rows: Dict[str, dict] = {}
    values: Dict[tuple, List[float]] = {}
    for trial in trials:
        rows.setdefault(
            trial.task_id,
            {"task_id": trial.task_id, "category": trial.task_category, "scores": {}},
        )
        values.setdefault((trial.task_id, trial.arm), []).append(trial.score)
    for (task_id, arm), scores in values.items():
        rows[task_id]["scores"][arm] = sum(scores) / len(scores)
    return [rows[task_id] for task_id in sorted(rows)]


def _num(value: Optional[float], digits: int) -> str:
    if value is None:
        return "—"
    return "{value:.{digits}f}".format(value=value, digits=digits)


def _signed(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return "{value:+.3f}".format(value=value)


def _pct(value: Optional[float], signed: bool = False) -> str:
    if value is None:
        return "—"
    template = "{value:+.1%}" if signed else "{value:.1%}"
    return template.format(value=value)


def _contrast_label(baseline: str, arm: str) -> str:
    labels = {
        ("A", "B"): "B−A extra checking",
        ("B", "C"): "C−B identity switch",
        ("C", "D"): "D−C dynamic routing",
    }
    return labels.get((baseline, arm), "{baseline}−{arm}".format(baseline=baseline, arm=arm))
