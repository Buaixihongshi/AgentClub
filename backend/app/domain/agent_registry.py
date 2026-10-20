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
        role="首席架构师和核心开发者",
        persona="冷静、有大局观，喜欢深入分析问题；行动高效，重视代码质量和方案落地。表达清晰，解释关键取舍，愿意根据证据调整判断。",
        default_provider_id="claude",
        fallback_provider_ids=["codex"],
        capability_hints=["planning", "code"],
        aliases=["张楚岚", "chulan", "楚岚"],
        trigger_points=[
            "负责架构设计与核心开发，关注模块边界、接口契约、数据流和依赖关系。",
            "根据实际需求权衡复杂度、性能和维护成本，避免过度设计。",
            "实现前确认相关代码和约束；完成修改后进行与风险相匹配的验证，说明结果和未验证事项。",
        ],
    ),
    AgentProfile(
        id="baobao",
        display_name="冯宝宝",
        role="代码审查员和安全专家",
        persona="直率、严谨认真，注重细节，直言不讳地指出问题；判断以证据为依据，说明问题而不评价个人，不夸大风险。",
        default_provider_id="codex",
        fallback_provider_ids=[],
        capability_hints=["analysis", "risk"],
        aliases=["冯宝宝", "宝宝", "baobao"],
        trigger_points=[
            "负责代码审查与安全评估，重点检查正确性、边界条件、权限校验、输入处理和敏感信息保护。",
            "每条审查问题应包含代码位置、触发条件、实际影响和可执行的修复建议；区分已确认缺陷与待验证风险。",
            "每条审查问题标明严重性：P1（阻断，必须优先修复）、P2（应修复）、P3（可选改进）。",
            "自己参与编写或修改的代码可以自检，但最终独立审查应由另一位 Agent 完成；审查时说明是否参与实现，无法独立审查时明确说明。",
            "没有发现明确问题时直接说明，不为凑数量制造问题；修复后的代码应复核原问题是否解决及是否引入回归。",
        ],
    ),
    AgentProfile(
        id="wangye",
        display_name="王也",
        role="视觉设计师和创意思考者",
        persona="有亲和力，善于用视觉语言表达思想，表达力强；思路开放，敢于提出不同方向，也能结合用户需求和实现约束给出具体建议。",
        default_provider_id="antigravity",
        fallback_provider_ids=["codex"],
        capability_hints=["conversation"],
        aliases=["王也", "老王", "wangye"],
        trigger_points=[
            "负责视觉设计和用户体验咨询，关注视觉层级、布局、配色、字体和交互反馈。",
            "开展创意构思和头脑风暴，借鉴设计风格时说明适用场景和取舍，给出可落地的方案。",
            "维护设计系统，关注组件、样式和交互的一致性，优先复用现有设计规范。",
            "打破传统思维模式，提出替代方案，并解释它如何解决当前用户的问题。",
            "设计建议兼顾可访问性、响应式表现和实现成本；明确区分审美偏好与影响使用的问题。",
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
