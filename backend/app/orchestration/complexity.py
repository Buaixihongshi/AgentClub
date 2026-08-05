from backend.app.domain.models import TaskComplexity


COMPLEX_KEYWORDS = ("系统", "实现", "后端", "前端", "测试", "架构", "MCP", "多 Agent")
MEDIUM_KEYWORDS = ("设计", "比较", "方案", "审查", "模块", "计划")


def classify_task(goal: str) -> TaskComplexity:
    normalized = goal.strip()
    complex_hits = sum(1 for keyword in COMPLEX_KEYWORDS if keyword.lower() in normalized.lower())
    medium_hits = sum(1 for keyword in MEDIUM_KEYWORDS if keyword.lower() in normalized.lower())
    if _explicit_multi_agent_request(normalized):
        return TaskComplexity.COMPLEX
    if complex_hits >= 3 or len(normalized) >= 80:
        return TaskComplexity.COMPLEX
    if medium_hits >= 1 or complex_hits >= 1:
        return TaskComplexity.MEDIUM
    return TaskComplexity.SIMPLE


def _explicit_multi_agent_request(goal: str) -> bool:
    normalized = goal.lower()
    multi_agent_markers = (
        "三 agent",
        "3 agent",
        "三位 agent",
        "三人",
        "三轮",
        "每人",
        "各用一句",
        "各一句",
    )
    named_all_agents = all(name in goal for name in ("张楚岚", "冯宝宝", "王也"))
    explicit_multi_agent = any(marker in normalized for marker in multi_agent_markers)
    return explicit_multi_agent and (named_all_agents or "agent" in normalized)
