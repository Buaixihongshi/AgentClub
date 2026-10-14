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

    if complexity == TaskComplexity.SIMPLE:
        return _require_available(
            [WorkflowStep(_best_single_agent(goal), "处理简单任务")],
            available,
        )

    if complexity == TaskComplexity.MEDIUM:
        primary = "baobao" if _looks_engineering(goal) else "chulan"
        reviewer = "wangye" if primary != "wangye" else "chulan"
        return _require_available(
            [
                WorkflowStep(primary, "主办任务"),
                WorkflowStep(reviewer, "复核风险和遗漏"),
            ],
            available,
        )

    return _require_available(
        [
            WorkflowStep("chulan", "拆解任务和制定协作顺序"),
            WorkflowStep("baobao", "补充具体做法、实例和可行性；按用户需求执行"),
            WorkflowStep("wangye", "进行分析和风险复盘"),
            WorkflowStep("chulan", "收束结论和下一步", is_closing=True),
        ],
        available,
    )


def _build_discussion_workflow(targets: List[str]) -> List[WorkflowStep]:
    """For `@A @B [@C ...]` style multi-target prompts: alternate the named
    agents for DISCUSSION_ALTERNATION_STEPS turns, then give the last-named
    agent an explicit closing turn. Last-mentioned closes by design (user
    chose option B in M03 dogfood discussion).
    """
    steps: List[WorkflowStep] = []
    for i in range(DISCUSSION_ALTERNATION_STEPS):
        steps.append(
            WorkflowStep(targets[i % len(targets)], "讨论：第 {n} 轮".format(n=i + 1))
        )
    steps.append(WorkflowStep(targets[-1], "收束讨论：给出共识或下一步", is_closing=True))
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


def _best_single_agent(goal: str) -> str:
    if _looks_engineering(goal):
        return "baobao"
    if any(word in goal for word in ("风险", "分析", "报错", "解释", "比较")):
        return "wangye"
    return "chulan"


def _looks_engineering(goal: str) -> bool:
    return any(word in goal for word in ("代码", "实现", "脚本", "文件", "测试", "修复"))
