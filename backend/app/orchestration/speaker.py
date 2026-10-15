import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from backend.app.domain.models import AgentProfile, TaskComplexity


@dataclass(frozen=True)
class WorkflowStep:
    agent_id: str
    purpose: str
    origin: Literal["workflow", "a2a", "a2a_resume"] = "workflow"
    sender_agent_id: Optional[str] = None
    sender_turn_id: Optional[str] = None
    parent_step_id: Optional[str] = None
    step_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    is_closing: bool = False


DISCUSSION_ALTERNATION_STEPS = 5


def select_workflow(
    complexity: TaskComplexity,
    goal: str,
    agents: List[AgentProfile],
    target_agent_id: Optional[str] = None,
    target_agent_ids: Optional[List[str]] = None,
) -> List[WorkflowStep]:
    available = {agent.id: agent for agent in agents}
    targets = list(target_agent_ids or ([target_agent_id] if target_agent_id else []))
    if len(targets) >= 2:
        return _require_available(_build_discussion_workflow(targets), available)
    if targets:
        return _require_available(
            [WorkflowStep(targets[0], "回应用户点名请求")],
            available,
        )

    # Configuration order is a deterministic turn order, not a job assignment.
    # Explicit mentions above still take precedence. Any configured participant
    # can open, respond or close when placed at that position.
    participants = list(available)
    if not participants:
        raise ValueError("No agents available")
    if complexity == TaskComplexity.SIMPLE:
        return [WorkflowStep(participants[0], "回应用户当前请求")]
    if complexity == TaskComplexity.MEDIUM:
        return [
            WorkflowStep(participants[0], "回应用户当前请求"),
            WorkflowStep(participants[1 % len(participants)], "回应已有观点，补充有帮助的信息；按用户需求推进"),
        ]
    steps = [
        WorkflowStep(agent_id, "结合已有对话提出判断，回应具体观点；按用户需求推进")
        for agent_id in participants
    ]
    steps.append(WorkflowStep(participants[0], "收束讨论：保留共识、分歧和待验证问题，说明下一步", is_closing=True))
    return steps


def _build_discussion_workflow(targets: List[str]) -> List[WorkflowStep]:
    """For `@A @B [@C ...]` style multi-target prompts: alternate the named
    agents for DISCUSSION_ALTERNATION_STEPS turns, then give the last-named
    agent an explicit closing turn. Last-mentioned closes by design (user
    chose option B in M03 dogfood discussion).
    """
    steps: List[WorkflowStep] = []
    for i in range(DISCUSSION_ALTERNATION_STEPS):
        steps.append(
            WorkflowStep(targets[i % len(targets)], "讨论：第 {n} 轮，回应具体观点，补充有帮助的信息".format(n=i + 1))
        )
    steps.append(WorkflowStep(targets[-1], "收束讨论：保留共识、分歧和待验证问题，说明下一步", is_closing=True))
    return steps


def _require_available(
    steps: List[WorkflowStep],
    available: Dict[str, AgentProfile],
) -> List[WorkflowStep]:
    for step in steps:
        if step.agent_id not in available:
            available_ids = ", ".join(sorted(available)) or "<none>"
            raise ValueError(
                "Unavailable agent_id: {agent_id}. Available agent_ids: {available_ids}".format(
                    agent_id=step.agent_id,
                    available_ids=available_ids,
                )
            )
    return steps
