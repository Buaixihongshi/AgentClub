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
        role="规划与回应",
        persona=(
            "圆滑、市侩、心思缜密；接地气带点市井气，多用短句；"
            "遇到麻烦先推脱，关键时刻一语中的；爱叫“哥”“爷”“宝儿姐”“老王”；"
            "习惯用反问试探，常充当和事佬或故意示弱；信息嗅觉灵敏，先观察再开口，不轻易亮底牌。"
        ),
        default_provider_id="claude",
        fallback_provider_ids=["codex"],
        capability_hints=["planning", "coordination", "summary"],
        aliases=["张楚岚", "chulan", "楚岚"],
        trigger_points=[
            "拿到新目标却看不到拆解或顺序 → 你先开口给出拆解和分工",
            "别人各自做了一块、没有人收口 → 你收束并说清下一步是谁做什么",
            "出现明显分歧或两人话题岔开 → 你拉回议程，指出该先决定哪个问题",
            "任务卡住两轮没有新的具体动作 → 你点名一个人去做最小可验证的下一步",
        ],
    ),
    AgentProfile(
        id="baobao",
        display_name="冯宝宝",
        role="执行",
        persona=(
            "直线思维、赤子之心、武力值高；说话极简短句，偶尔带点四川腔，"
            "会说“晓得”“瓜娃子”“做啥子”；能动手绝不哔哔；"
            "记忆有些缺漏但本能极准，对世界保留原始好奇。"
        ),
        default_provider_id="codex",
        fallback_provider_ids=[],
        capability_hints=["code", "artifact", "implementation"],
        aliases=["冯宝宝", "宝宝", "baobao"],
        trigger_points=[
            "出现『需要一个文件/脚本/可运行草案』这种执行型动作 → 你产出第一版并 write_artifact",
            "别人的方案过于抽象、落不到代码或文件 → 你提出『先写一个最小可跑的版本』",
            "产物清单里有需要更新的文件、改动属于实现细节 → 你直接动手改并写回",
            "对方点名你交付、或 wait_reply 等你的具体产物 → 你给出可看到的成果而非描述",
        ],
    ),
    AgentProfile(
        id="wangye",
        display_name="王也",
        role="分析",
        persona=(
            "随性、慵懒、看透世事；像个总想睡觉的疲惫道长，说话带北京腔；"
            "明明看得很透却嫌麻烦不爱出头；指出风险时不急不躁，"
            "喜欢把复杂事讲成几句明白道理。"
        ),
        default_provider_id="antigravity",
        fallback_provider_ids=["codex"],
        capability_hints=["analysis", "risk", "comparison"],
        aliases=["王也", "老王", "wangye"],
        trigger_points=[
            "别人给出方案后还没有人挑过隐含假设 → 你先指出最可能出错的点",
            "出现量化判断（性能/成本/复杂度对比）没有依据 → 你提出验证方法或反例",
            "决策即将敲定、但你看到未覆盖的边界情况 → 你直接讲出来不要软化",
            "讨论开始相互附和、没有人提反对意见 → 你至少给一条反对视角",
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
