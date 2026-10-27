"""Explicit orchestration plans for evaluation arms A/B/C/D.

The shipped product uses ``AUTO`` (complexity-classified workflow + optional
A2A).  Evaluation needs stable, comparable arms, so each arm is expressed as a
deterministic plan here rather than being inferred from the goal text:

A. SINGLE     one agent, one turn.
B. REFLECTION one agent: draft -> check one -> check two -> revise (4 turns).
C. FIXED      P1 draft -> P2 check one -> P3 check two -> P1 revise (4 turns).
D. DYNAMIC    the same 4-turn skeleton as C, plus up to 2 dynamic A2A turns.

B and C share stage purposes; C changes who performs the checks.  D keeps the
same skeleton and lets agents add handoffs.  Keeping the lead and provider
identical across arms is the caller's responsibility (the evaluation runner
pins both).
"""
from dataclasses import dataclass
from typing import List

from backend.app.domain.models import (
    AgentProfile,
    Mission,
    OrchestrationMode,
)
from backend.app.orchestration.speaker import WorkflowStep, select_workflow
from backend.app.orchestration.interaction import is_casual_turn


@dataclass(frozen=True)
class OrchestrationPlan:
    mode: OrchestrationMode
    steps: List[WorkflowStep]
    agent_ids: List[str]
    max_a2a_invocations: int
    max_turns: int

    @property
    def a2a_enabled(self) -> bool:
        return self.max_a2a_invocations > 0


def plan_orchestration(
    mission: Mission,
    agents: List[AgentProfile],
    *,
    default_max_a2a: int,
    default_max_turns: int,
) -> OrchestrationPlan:
    """Return the workflow, allowed participants and A2A budget for a mission."""
    available = {agent.id: agent for agent in agents}
    if not available:
        raise ValueError("No agents available")

    config = mission.orchestration
    mode = config.mode if config is not None else OrchestrationMode.AUTO
    requested = list(config.participant_ids) if config is not None else []

    if mode == OrchestrationMode.AUTO:
        workflow = select_workflow(
            mission.complexity,
            mission.goal,
            agents,
            target_agent_id=mission.target_agent_id,
            target_agent_ids=list(mission.target_agent_ids) or None,
        )
        return OrchestrationPlan(
            mode=mode,
            steps=workflow,
            agent_ids=list(available),
            max_a2a_invocations=0 if is_casual_turn(mission.goal, agents) else max(0, default_max_a2a),
            max_turns=max(0, default_max_turns),
        )

    participants = _resolve_participants(requested, available)
    lead = participants[0]

    if mode == OrchestrationMode.SINGLE:
        return OrchestrationPlan(
            mode=mode,
            steps=[
                WorkflowStep(
                    lead,
                    "独立完成用户请求：直接给出完整、可执行的最终答案；"
                    "不需要征求其他角色意见。",
                    is_closing=True,
                )
            ],
            agent_ids=participants,
            max_a2a_invocations=0,
            max_turns=1,
        )

    if mode == OrchestrationMode.REFLECTION:
        return OrchestrationPlan(
            mode=mode,
            steps=[
                WorkflowStep(lead, _DRAFT_PURPOSE),
                WorkflowStep(lead, _CHECK_ONE_PURPOSE),
                WorkflowStep(lead, _CHECK_TWO_PURPOSE),
                WorkflowStep(lead, _REVISE_PURPOSE, is_closing=True),
            ],
            agent_ids=participants,
            max_a2a_invocations=0,
            max_turns=4,
        )

    if mode == OrchestrationMode.FIXED:
        checkers = _check_speakers(participants)
        steps = [
            WorkflowStep(lead, _DRAFT_PURPOSE),
            WorkflowStep(checkers[0], _CHECK_ONE_PURPOSE),
            WorkflowStep(checkers[1], _CHECK_TWO_PURPOSE),
            WorkflowStep(lead, _REVISE_PURPOSE, is_closing=True),
        ]
        return OrchestrationPlan(
            mode=mode,
            steps=steps,
            agent_ids=participants,
            max_a2a_invocations=0,
            max_turns=4,
        )

    if mode == OrchestrationMode.DYNAMIC:
        checkers = _check_speakers(participants)
        steps = [
            WorkflowStep(lead, _DRAFT_PURPOSE),
            WorkflowStep(checkers[0], _CHECK_ONE_PURPOSE),
            WorkflowStep(checkers[1], _CHECK_TWO_PURPOSE),
            WorkflowStep(lead, _REVISE_PURPOSE, is_closing=True),
        ]
        return OrchestrationPlan(
            mode=mode,
            steps=steps,
            agent_ids=participants,
            max_a2a_invocations=min(2, max(0, default_max_a2a)),
            max_turns=4,
        )

    raise ValueError("Unsupported orchestration mode: {mode}".format(mode=mode))


_DRAFT_PURPOSE = (
    "起草：独立完成用户请求，给出你认为最好的初稿。"
    "先不要自我评价，也不要邀请其他角色。"
)
_CHECK_ONE_PURPOSE = (
    "检查一：基于用户请求和初稿，检查事实错误、遗漏和约束违反；"
    "只列具体问题或说明没有发现问题，不要重写答案。"
)
_CHECK_TWO_PURPOSE = (
    "检查二：换一个角度复查初稿和检查一，重点看边界条件、交付格式"
    "和未验证假设；只列具体问题或说明没有发现问题，不要重写答案。"
)
_REVISE_PURPOSE = (
    "修订：根据检查意见重写最终答案，修正错误、补全遗漏；"
    "只输出最终答案，不要复盘检查过程。"
)


def _check_speakers(participants: List[str]) -> List[str]:
    """Pick two reviewers for the shared 4-turn skeleton.

    With three participants the skeleton is P1 draft -> P2 check one ->
    P3 check two -> P1 revise.  With two, the second participant performs both
    checks so B and C still have the same number of host turns.
    """
    if len(participants) < 2:
        raise ValueError("fixed/dynamic orchestration needs at least two participants")
    second = participants[1]
    third = participants[2] if len(participants) > 2 else participants[1]
    return [second, third]


def _resolve_participants(
    requested: List[str],
    available: dict,
) -> List[str]:
    if requested:
        missing = [agent_id for agent_id in requested if agent_id not in available]
        if missing:
            raise ValueError(
                "Unavailable orchestration participant(s): {missing}. "
                "Available agent_ids: {available}".format(
                    missing=", ".join(missing),
                    available=", ".join(sorted(available)),
                )
            )
        return list(requested)
    if not available:
        raise ValueError("No agents available")
    return list(available)
