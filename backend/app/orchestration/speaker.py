import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from backend.app.domain.models import AgentProfile


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


def resume_purpose(target: str, purpose: str) -> str:
    """Host-owned single line naming what a resumed requester waited for."""
    flat = " ".join(str(purpose).split())
    if len(flat) > 80:
        flat = flat[:79] + "…"
    return "承接 {target} 对『{purpose}』的回复".format(target=target, purpose=flat)


def select_workflow(
    agents: List[AgentProfile],
    target_agent_ids: Optional[List[str]] = None,
    *,
    last_speaker_id: Optional[str] = None,
) -> List[WorkflowStep]:
    """Who answers the user's message.

    Named agents answer once each, in mention order; later speakers see the
    earlier replies. Without a name, whoever spoke last in the conversation
    keeps talking, like a person continuing a chat. A new conversation starts
    with the first configured agent. Anything beyond that is an explicit A2A
    hand-off decided by the agents, not a host-scripted round.
    """
    available = {agent.id: agent for agent in agents}
    if not available:
        raise ValueError("No agents available")
    if target_agent_ids:
        return _require_available(
            [WorkflowStep(agent_id, "回应用户点名的这句话") for agent_id in dict.fromkeys(target_agent_ids)],
            available,
        )
    agent_id = last_speaker_id if last_speaker_id in available else next(iter(available))
    return [WorkflowStep(agent_id, "接着和用户聊，回应用户这句话")]


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
