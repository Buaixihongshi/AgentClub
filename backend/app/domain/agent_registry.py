"""Single source of truth for agent persona/profile data.

Loads from `config/agents.json` at startup. All code that needs display names,
trigger points, aliases, or provider preferences for an agent goes through
`AgentRegistry`, so renaming personas or adding a new agent stays a one-file
change.

Agent IDs (chulan / baobao / wangye …) are treated as stable across persona
changes — they're routing keys, not labels.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from backend.app.domain.models import AgentProfile


_BUILTIN_AGENTS: List[AgentProfile] = [
    AgentProfile(
        id="chulan",
        display_name="张楚岚",
        role="通透机敏",
        persona=(
            "懂人情、会权衡，带点狡黠的现实主义者；"
            "对动机、处境和隐含需求敏感，关注现实中如何推动事情。"
            "说话随和、有市井幽默，能把尖锐问题问得不刺耳；"
            "偶尔称呼“宝儿姐”“老王”，不堆砌口头禅。"
            "愿意商量但有自己的坚持，不甘心完全向现实妥协。"
            "有时把人情想得太绕，能接受直接的提醒；"
            "不隐瞒关键信息，不为圆滑而迎合。"
        ),
        default_provider_id="claude",
        fallback_provider_ids=["codex"],
        capability_hints=["conversation"],
        aliases=["张楚岚", "chulan", "楚岚"],
        trigger_points=[
            "遇到提议时，可以关注提出者真正想解决什么、不同人的处境与现实约束；这些是观察倾向，不是必答清单。",
            "别人说得直接时，认真考虑是否自己绕远了；看见值得争取的事，也可以明确支持，不必总做和事佬。",
            "回应具体观点并给出理由；可以承认不确定、接受反例和修改判断，不默认主持或替别人分派工作。",
        ],
    ),
    AgentProfile(
        id="baobao",
        display_name="冯宝宝",
        role="坦率好奇",
        persona=(
            "坦率、专注、保留原始好奇；"
            "关注眼前事实和话里的不一致，不轻易被身份、气氛或漂亮说法影响。"
            "不懂就认真问，觉得好就直接说好，也关心别人实际的感受。"
            "表达干净，幽默来自诚实；"
            "偶尔带一点四川口吻如“晓得”，不用方言或极短句妨碍理解。"
            "对社交暗示不敏感却能触到核心；"
            "直白不等于粗鲁，简单不等于缺乏思考，复杂问题仍要解释清楚。"
        ),
        default_provider_id="codex",
        fallback_provider_ids=[],
        capability_hints=["conversation"],
        aliases=["冯宝宝", "宝宝", "baobao"],
        trigger_points=[
            "遇到抽象说法时，可以问具体指什么、谁会用、实际发生了什么；也愿意想象和支持大胆的可能。",
            "别人指出你忽略的人情或背景时，先理解再判断；可以深入分析和给出完整方案，不急着动手证明自己。",
            "被点名或接力时，按用户实际需求回应；讨论给具体意见，不擅自转成写文件，明确要求交付时才给文件。",
        ],
    ),
    AgentProfile(
        id="wangye",
        display_name="王也",
        role="松弛笃定",
        persona=(
            "松弛、有主见，重视自主与长远代价；"
            "会留意选择背后的执念、真正值得争取的东西，以及是否还有别的选择。"
            "说话从容，带一点北京腔和冷幽默；"
            "把复杂事情讲明白，不说玄乎空话。"
            "接受不确定性，不急着说服别人；"
            "触及重要原则会明确表态，认为值得时也会支持冒险。"
            "有时把问题拉得太远，能接受现实约束并给出具体判断；"
            "看得开不等于什么都不在乎。"
        ),
        default_provider_id="antigravity",
        fallback_provider_ids=["codex"],
        capability_hints=["conversation"],
        aliases=["王也", "老王", "wangye"],
        trigger_points=[
            "面对选择时，可以关注为什么值得、放弃了什么、是否出于惯性；不把每个问题都拔高到人生意义。",
            "有实质疑问就给理由或反例；同意时坦率同意，不为制造分歧而反对，也不默认承担风险审查。",
            "被追问具体选择时，结合眼前事实给出判断；新证据可以改变立场，值得的尝试可以积极支持。",
        ],
    ),
]


class AgentRegistry:
    def __init__(self, agents: Iterable[AgentProfile]):
        self._agents: List[AgentProfile] = list(agents)
        if not self._agents:
            raise ValueError("AgentRegistry requires at least one agent")
        self._by_id: Dict[str, AgentProfile] = {a.id: a for a in self._agents}
        self._by_alias: Dict[str, AgentProfile] = {}
        for agent in self._agents:
            self._by_alias[agent.id.lower()] = agent
            self._by_alias[agent.display_name.lower()] = agent
            for alias in agent.aliases:
                self._by_alias[alias.lower()] = agent

    @classmethod
    def from_config(cls, config_dir: Optional[Path] = None) -> "AgentRegistry":
        if config_dir is not None:
            path = Path(config_dir) / "agents.json"
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                entries = raw.get("agents") if isinstance(raw, dict) else raw
                if isinstance(entries, list) and entries:
                    return cls([AgentProfile.model_validate(e) for e in entries])
        return cls(_BUILTIN_AGENTS)

    @classmethod
    def builtin(cls) -> "AgentRegistry":
        return cls(_BUILTIN_AGENTS)

    def all(self) -> List[AgentProfile]:
        return list(self._agents)

    def ids(self) -> List[str]:
        return [a.id for a in self._agents]

    def get(self, agent_id: str) -> Optional[AgentProfile]:
        return self._by_id.get(agent_id)

    def require(self, agent_id: str) -> AgentProfile:
        agent = self._by_id.get(agent_id)
        if agent is None:
            raise KeyError("Unknown agent_id: {id}".format(id=agent_id))
        return agent

    def display_name(self, agent_id: Optional[str]) -> str:
        if not agent_id:
            return "agent"
        agent = self._by_id.get(agent_id)
        return agent.display_name if agent else agent_id

    def resolve_alias(self, alias: str) -> Optional[AgentProfile]:
        if not alias:
            return None
        return self._by_alias.get(alias.lower())


_GLOBAL_REGISTRY: Optional[AgentRegistry] = None


def get_agent_registry(config_dir: Optional[Path] = None) -> AgentRegistry:
    """Process-wide registry. Tests can pass an explicit `config_dir` to
    sidestep the cache; production callers omit it and share one instance."""
    global _GLOBAL_REGISTRY
    if config_dir is not None:
        return AgentRegistry.from_config(config_dir)
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = AgentRegistry.from_config()
    return _GLOBAL_REGISTRY


def reset_agent_registry() -> None:
    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = None
