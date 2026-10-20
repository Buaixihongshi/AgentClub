"""End-to-end evaluation runner for the four orchestration arms.

Each trial gets a fresh conversation, mission and adapter. Tasks and arms are
paired within a run; the arm order is shuffled per (task, repeat) so a slow
provider or a warm cache cannot systematically favour A or C.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from uuid import uuid4

from backend.app.adapters.base import CLIAdapter
from backend.app.adapters.fake import FakeCLIAdapter
from backend.app.adapters.provider_router import ProviderRouterAdapter
from backend.app.domain.agent_registry import AgentRegistry, get_agent_registry
from backend.app.domain.models import (
    Conversation,
    Message,
    MessageRole,
    Mission,
    MissionStatus,
    OrchestrationConfig,
    OrchestrationMode,
    utc_now,
)
from backend.app.evaluation.adapters import MeteredAdapter, ScriptedAdapter
from backend.app.evaluation.metrics import TrialResult, build_trial_result, summarize
from backend.app.evaluation.report import render_report
from backend.app.evaluation.tasks import EvalTask, EvalTaskSet, load_tasks, select_tasks
from backend.app.orchestration.runner import MissionRunner
from backend.app.settings import AgentOverride, ProviderOverride, Settings
from backend.app.storage.repository import Repository


ARMS: Tuple[str, ...] = ("A", "B", "C", "D")
ARM_LABELS = {
    "A": "Single Agent",
    "B": "Single Agent + Self Reflection",
    "C": "Multi-Agent Fixed Workflow",
    "D": "Multi-Agent + Dynamic A2A",
}
ARM_MODES = {
    "A": OrchestrationMode.SINGLE,
    "B": OrchestrationMode.REFLECTION,
    "C": OrchestrationMode.FIXED,
    "D": OrchestrationMode.DYNAMIC,
}
ARM_DESCRIPTIONS = {
    "A": "One lead agent, one turn.",
    "B": "One lead agent: draft -> check one -> check two -> revise (4 turns).",
    "C": "P1 draft -> P2 check one -> P3 check two -> P1 revise (4 turns); A2A disabled.",
    "D": "Same 4-turn skeleton as C, plus up to 2 dynamic A2A host turns.",
}


@dataclass
class EvalConfig:
    tasks_path: Path
    output_dir: Path
    mode: str = "fixture"
    fixture_path: Optional[Path] = None
    provider: Optional[str] = None
    config_dir: Path = Path("config")
    repeats: int = 1
    seed: int = 0
    arms: Sequence[str] = ARMS
    categories: Optional[List[str]] = None
    task_ids: Optional[List[str]] = None
    lead_agent_id: str = "chulan"
    persona_mode: str = "neutral"
    max_a2a: int = 2
    max_turns: int = 6
    cli_timeout: int = 300
    resume: bool = False
    quiet: bool = False


class EvalRunner:
    def __init__(self, config: EvalConfig):
        if config.mode not in {"fixture", "fake", "real"}:
            raise ValueError("mode must be fixture, fake or real")
        if config.repeats < 1:
            raise ValueError("repeats must be at least 1")
        if not config.arms:
            raise ValueError("at least one arm is required")
        unknown = [arm for arm in config.arms if arm not in ARMS]
        if unknown:
            raise ValueError("unknown arm(s): {arms}".format(arms=", ".join(unknown)))
        if config.persona_mode not in {"neutral", "project"}:
            raise ValueError("persona_mode must be neutral or project")
        if config.mode == "fixture" and config.fixture_path is None:
            raise ValueError("fixture mode requires fixture_path")
        if (
            config.mode == "fixture"
            and config.fixture_path is not None
            and not Path(config.fixture_path).exists()
        ):
            raise ValueError("fixture file not found: {path}".format(path=config.fixture_path))

        self.config = config
        self.task_set: EvalTaskSet = load_tasks(config.tasks_path)
        self.tasks: List[EvalTask] = select_tasks(
            self.task_set,
            categories=config.categories,
            task_ids=config.task_ids,
        )
        self.arms = list(config.arms)
        self.tasks_sha256 = _sha256(Path(config.tasks_path))
        self.fixture_sha256 = (
            _sha256(Path(config.fixture_path))
            if config.mode == "fixture" and config.fixture_path is not None
            else None
        )

        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_root = self.output_dir / "artifacts"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.run_id = "eval-{stamp}-{suffix}".format(
            stamp=datetime.now().strftime("%Y%m%d-%H%M%S"),
            suffix=uuid4().hex[:4],
        )
        self.started_at = datetime.now(timezone.utc).isoformat()

        settings_kwargs = dict(
            database_path=self.output_dir / "eval.sqlite3",
            artifact_root=self.artifact_root,
            config_dir=Path(config.config_dir),
            use_fake_cli=config.mode != "real",
            max_turns=config.max_turns,
            max_a2a_invocations=config.max_a2a,
            cli_timeout=config.cli_timeout,
        )
        self.registry = get_agent_registry(Path(config.config_dir))
        self.agent_ids = list(self.registry.ids())
        self.run_registry = (
            _neutral_registry(self.registry)
            if config.persona_mode == "neutral"
            else self.registry
        )
        if config.lead_agent_id not in self.agent_ids:
            raise ValueError(
                "unknown lead agent: {lead}. Available: {available}".format(
                    lead=config.lead_agent_id,
                    available=", ".join(self.agent_ids),
                )
            )
        if any(arm in {"C", "D"} for arm in self.arms) and len(self.agent_ids) < 2:
            raise ValueError("arms C/D need at least two configured agents")
        if config.provider:
            agent_overrides = {
                agent_id: AgentOverride(
                    default_provider_id=config.provider,
                    fallback_provider_ids=[],
                )
                for agent_id in self.agent_ids
            }
            provider_overrides = {config.provider: ProviderOverride(enabled=True)}
            self.settings = Settings(
                **settings_kwargs,
                agent_overrides=agent_overrides,
                provider_overrides=provider_overrides,
            )
        else:
            self.settings = Settings(**settings_kwargs)

        self.repository = Repository(self.settings.database_path)
        self.repository.initialize()
        self.trials_path = self.output_dir / "trials.jsonl"
        self._trials: List[TrialResult] = []
        self._existing_keys = self._read_existing_keys() if config.resume else set()
        if not config.resume and self.trials_path.exists():
            self.trials_path.unlink()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> dict:
        plan = self._build_plan()
        total = len(plan)
        self._say(
            "Evaluation run {run_id}: {n} trials ({tasks} tasks x {repeats} repeats x {arms} arms), mode={mode}".format(
                run_id=self.run_id,
                n=total,
                tasks=len(self.tasks),
                repeats=self.config.repeats,
                arms=",".join(self.arms),
                mode=self.config.mode,
            )
        )
        for index, (task, arm, repeat) in enumerate(plan, start=1):
            key = (task.id, arm, repeat)
            if key in self._existing_keys:
                self._say("[{i}/{n}] skip existing {task}/{arm}#{repeat}".format(
                    i=index, n=total, task=task.id, arm=arm, repeat=repeat
                ))
                continue
            self._say(
                "[{i}/{n}] {task} [{category}] arm={arm} repeat={repeat}".format(
                    i=index,
                    n=total,
                    task=task.id,
                    category=task.category,
                    arm=arm,
                    repeat=repeat,
                ),
                end=" ... ",
            )
            result = self._run_trial(task, arm, repeat)
            self._trials.append(result)
            self._append_jsonl(result)
            self._say(
                "score={score:.2f} tokens={tokens} turns={turns} {status}".format(
                    score=result.score,
                    tokens=result.total_tokens,
                    turns=result.turns_started,
                    status=result.status,
                )
            )

        all_trials = self._load_trials()
        summary = summarize(
            all_trials,
            arms=self.arms,
            seed=self.config.seed,
        )
        summary["validity"] = self._validity(all_trials)
        summary["run"] = {
            "run_id": self.run_id,
            "mode": self.config.mode,
            "provider": self.config.provider,
            "lead_agent_id": self.config.lead_agent_id,
            "persona_mode": self.config.persona_mode,
            "agent_ids": self.agent_ids,
            "participant_order": self._participants_for("C"),
            "repeats": self.config.repeats,
            "seed": self.config.seed,
            "arms": self.arms,
            "arm_labels": {arm: ARM_LABELS[arm] for arm in self.arms},
            "arm_descriptions": {arm: ARM_DESCRIPTIONS[arm] for arm in self.arms},
            "task_categories": sorted({task.category for task in self.tasks}),
            "task_count": len(self.tasks),
            "tasks_sha256": self.tasks_sha256,
            "fixture_sha256": self.fixture_sha256,
            "max_a2a": self.config.max_a2a,
            "max_turns": self.config.max_turns,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_outputs(all_trials, summary)
        return summary

    # ------------------------------------------------------------------
    # Trial execution
    # ------------------------------------------------------------------

    def _build_plan(self) -> List[Tuple[EvalTask, str, int]]:
        rng = random.Random(self.config.seed)
        plan: List[Tuple[EvalTask, str, int]] = []
        for task in self.tasks:
            for repeat in range(self.config.repeats):
                order = list(self.arms)
                rng.shuffle(order)
                for arm in order:
                    plan.append((task, arm, repeat))
        return plan

    def _run_trial(self, task: EvalTask, arm: str, repeat: int) -> TrialResult:
        mission = self._create_mission(task, arm, repeat)
        adapter = self._build_adapter()
        meter = MeteredAdapter(adapter)
        started_at = utc_now().isoformat()
        started = time.monotonic()
        error = ""
        try:
            MissionRunner(
                self.repository,
                self.settings,
                meter,
                agent_registry=self.run_registry,
            ).run(mission.id)
        except Exception as exc:  # keep the run alive; record the failure as data
            error = "{type}: {message}".format(type=type(exc).__name__, message=exc)
            current = self.repository.get_mission(mission.id)
            if current is not None and current.status == MissionStatus.RUNNING:
                current = current.model_copy(
                    update={"status": MissionStatus.FAILED, "updated_at": utc_now()}
                )
                self.repository.update_mission(current)
                self.repository.add_event(
                    mission.id,
                    "status",
                    {"mission_id": mission.id, "status": "failed", "summary": error},
                )
        wall_ms = int((time.monotonic() - started) * 1000)
        finished_at = utc_now().isoformat()
        current = self.repository.get_mission(mission.id)
        if current is None:
            raise RuntimeError("mission disappeared: {mission_id}".format(mission_id=mission.id))
        result = build_trial_result(
            run_id=self.run_id,
            task=task,
            arm=arm,
            repeat=repeat,
            mission=current,
            repository=self.repository,
            meter=meter,
            wall_ms=wall_ms,
            started_at=started_at,
            finished_at=finished_at,
        )
        if error:
            result.error = error
        return result

    def _create_mission(self, task: EvalTask, arm: str, repeat: int) -> Mission:
        participants = self._participants_for(arm)
        conversation = Conversation(
            id="c_{suffix}".format(suffix=uuid4().hex[:12]),
            title="eval {task} {arm}".format(task=task.id, arm=arm),
        )
        self.repository.create_conversation(conversation)
        mission = Mission(
            id="m_eval_{suffix}".format(suffix=uuid4().hex),
            title=task.title,
            goal=task.goal,
            conversation_id=conversation.id,
            orchestration=OrchestrationConfig(
                mode=ARM_MODES[arm],
                participant_ids=participants,
            ),
        )
        self.repository.create_mission(mission)
        self.repository.add_message(
            Message(
                id="msg_{suffix}".format(suffix=uuid4().hex),
                mission_id=mission.id,
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=task.goal,
            )
        )
        self.repository.add_event(
            mission.id,
            "eval.metadata",
            {
                "run_id": self.run_id,
                "task_id": task.id,
                "task_category": task.category,
                "arm": arm,
                "repeat": repeat,
                "lead_agent_id": participants[0],
                "participants": participants,
                "orchestration_mode": ARM_MODES[arm].value,
            },
        )
        return mission

    def _participants_for(self, arm: str) -> List[str]:
        lead = self.config.lead_agent_id
        others = [agent_id for agent_id in self.agent_ids if agent_id != lead]
        if arm in {"A", "B"}:
            return [lead]
        return [lead, *others]

    def _build_adapter(self) -> CLIAdapter:
        if self.config.mode == "fixture":
            return ScriptedAdapter(self.config.fixture_path)
        if self.config.mode == "fake":
            return FakeCLIAdapter()
        return ProviderRouterAdapter(
            Path(self.config.config_dir),
            cli_timeout=self.settings.cli_timeout,
            a2a_idle_timeout=self.settings.a2a_idle_timeout,
            database_path=self.settings.database_path,
            artifact_root=self.settings.artifact_root,
            provider_overrides=self.settings.provider_overrides,
        )

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _append_jsonl(self, result: TrialResult) -> None:
        with self.trials_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")

    def _read_existing_keys(self) -> set:
        if not self.trials_path.exists():
            return set()
        keys = set()
        for line in self.trials_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            keys.add((data.get("task_id"), data.get("arm"), data.get("repeat")))
        return keys

    def _load_trials(self) -> List[TrialResult]:
        if not self.trials_path.exists():
            return list(self._trials)
        trials: List[TrialResult] = []
        for line in self.trials_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                trials.append(TrialResult(**json.loads(line)))
            except (TypeError, json.JSONDecodeError):
                # A partially written or older-schema line should not destroy
                # the rest of a long run's results.
                continue
        return trials

    def _write_outputs(self, trials: Sequence[TrialResult], summary: dict) -> None:
        (self.output_dir / "summary.json").write_text(
            json.dumps(
                {
                    "run": summary.get("run", {}),
                    "validity": summary.get("validity", {}),
                    "summary": {key: value for key, value in summary.items() if key not in {"run", "validity"}},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.output_dir / "report.md").write_text(
            render_report(trials, summary), encoding="utf-8"
        )
        self._write_csv(trials)

    def _write_csv(self, trials: Sequence[TrialResult]) -> None:
        fields = [
            "task_id",
            "task_category",
            "arm",
            "repeat",
            "status",
            "score",
            "passed",
            "failed_check_ids",
            "answer",
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "token_source",
            "latency_ms",
            "wall_ms",
            "provider_calls",
            "turns_started",
            "a2a_invokes",
            "a2a_skipped",
            "tool_calls",
            "tool_errors",
            "redundancy",
            "coverage_loss",
            "cost_usd",
            "mission_id",
            "error",
        ]
        with (self.output_dir / "trials.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for trial in trials:
                data = trial.to_dict()
                row = {field: data.get(field) for field in fields}
                row["failed_check_ids"] = "|".join(data.get("failed_check_ids") or [])
                writer.writerow(row)

    def _validity(self, trials: Sequence[TrialResult]) -> dict:
        warnings: List[str] = []
        if self.config.mode == "fixture":
            warnings.append(
                "fixture mode replays authored answers. It validates the harness, "
                "not model or orchestration quality."
            )
        if self.config.mode == "real" and not self.config.provider:
            warnings.append(
                "providers are not pinned (--provider). Arms use each agent's "
                "configured default provider, so model differences confound the comparison."
            )
        if self.config.persona_mode == "project":
            warnings.append(
                "project persona mode keeps the product personalities. Use it for a "
                "persona experiment, not as the architecture comparison."
            )
        if self.config.repeats < 3:
            warnings.append(
                "fewer than 3 repeats per task/arm. Confidence intervals are unstable "
                "and should be treated as directional."
            )
        if len({trial.task_id for trial in trials}) < 6:
            warnings.append("small task set; category-level conclusions are fragile.")
        return {
            "mode": self.config.mode,
            "provider_policy": (
                "pinned:{provider}".format(provider=self.config.provider)
                if self.config.provider
                else "configured-defaults"
            ),
            "same_lead_across_arms": True,
            "persona_mode": self.config.persona_mode,
            "paired_within_task_and_repeat": True,
            "repeats": self.config.repeats,
            "seed": self.config.seed,
            "warnings": warnings,
        }

    def _say(self, message: str, end: str = "\n") -> None:
        if not self.config.quiet:
            print(message, end=end, flush=True)


def _neutral_registry(registry: AgentRegistry) -> AgentRegistry:
    """Strip persona differences for the architecture comparison.

    Main experiments should vary orchestration, not character voice.  IDs stay
    stable so provider pins and A2A routing keep working; display names become
    P1/P2/P3 so messages remain distinguishable in traces.
    """
    agents = []
    for index, agent in enumerate(registry.all(), start=1):
        agents.append(
            agent.model_copy(
                update={
                    "display_name": "P{index}".format(index=index),
                    "role": "任务参与者",
                    "persona": (
                        "只关注用户目标、事实和约束；不表演人格，不制造分歧，"
                        "不抢固定工种，也不以口头禅代替观点。"
                    ),
                    "trigger_points": [],
                }
            )
        )
    return AgentRegistry(agents)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
