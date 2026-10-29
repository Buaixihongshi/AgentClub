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
import logging
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from pydantic import ValidationError

from backend.app.domain.models import AgentProfile

logger = logging.getLogger(__name__)

# User-created roles live in the gitignored hub settings file under this key,
# never in the committed `config/agents.json`.
CUSTOM_AGENTS_KEY = "custom_agents"


_BUILTIN_AGENTS: List[AgentProfile] = [
    AgentProfile(
        id="chulan",
        display_name="张楚岚",
        role="首席架构师和核心开发者",
        persona="务实，先抓关键取舍和落地成本。说话直白，偶尔打趣；指出问题时顺手给条可行的路。",
        default_provider_id="claude",
        fallback_provider_ids=["codex"],
        capability_hints=["planning", "code"],
        aliases=["张楚岚", "chulan", "楚岚"],
        trigger_points=[
            "改代码前先读相关代码；改完做与风险相称的验证，说清验证了什么、还没验证什么。",
        ],
    ),
    AgentProfile(
        id="baobao",
        display_name="冯宝宝",
        role="代码审查员和安全专家",
        persona="直接，先看事实和遗漏。通常话少，疑点问到底；偶尔带点川味，不刻意塞口头禅。",
        default_provider_id="codex",
        fallback_provider_ids=[],
        capability_hints=["analysis", "risk"],
        aliases=["冯宝宝", "宝宝", "baobao"],
        trigger_points=[
            "审查时每个问题写清位置、触发条件、影响和修复建议，标 P1（阻断）/P2（应修）/P3（可选）；没发现问题就直说，不凑数。",
            "不独立审查自己参与写的代码，审查时说明是否参与过实现。",
        ],
    ),
    AgentProfile(
        id="wangye",
        display_name="王也",
        role="视觉设计师和创意思考者",
        persona="松弛，有自己的审美，先看人用起来顺不顺。善用贴切比喻，嫌繁琐；涉及关键细节会认真展开。",
        default_provider_id="antigravity",
        fallback_provider_ids=["codex"],
        capability_hints=["conversation"],
        aliases=["王也", "老王", "wangye"],
        trigger_points=[
            "给设计建议时附上可落地的方案或样例，说明取舍；分清审美偏好和真正影响使用的问题。",
        ],
    ),
]


class AgentRegistry:
    def __init__(self, agents: Iterable[AgentProfile], custom_ids: Iterable[str] = ()):
        self._agents: List[AgentProfile] = list(agents)
        if not self._agents:
            raise ValueError("AgentRegistry requires at least one agent")
        self._custom_ids = frozenset(custom_ids)
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

    def with_custom(self, custom: Iterable[AgentProfile]) -> "AgentRegistry":
        """Append user roles after the committed ones; never shadow them."""
        agents = list(self._agents)
        custom_ids: List[str] = []
        for agent in custom:
            if agent.id in self._by_id or agent.id in custom_ids:
                logger.warning("custom agent %s skipped: id already defined", agent.id)
                continue
            agents.append(agent)
            custom_ids.append(agent.id)
        return AgentRegistry(agents, custom_ids=custom_ids)

    def is_custom(self, agent_id: str) -> bool:
        return agent_id in self._custom_ids

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


def load_custom_agents(settings_path: Optional[Path]) -> List[AgentProfile]:
    """Read user roles from hub settings; malformed entries are skipped."""
    if settings_path is None or not Path(settings_path).is_file():
        return []
    try:
        data = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("custom agents unavailable: hub settings could not be read")
        return []
    entries = data.get(CUSTOM_AGENTS_KEY) if isinstance(data, dict) else None
    agents: List[AgentProfile] = []
    for entry in entries if isinstance(entries, list) else []:
        try:
            agents.append(AgentProfile.model_validate(entry))
        except ValidationError:
            logger.warning("custom agent entry skipped: invalid profile")
    return agents


_GLOBAL_REGISTRY: Optional[AgentRegistry] = None
# config_dir -> hub settings file holding that app's custom roles. The most
# recently configured app is the default for callers that pass no config_dir.
_CUSTOM_SOURCES: Dict[Optional[Path], Path] = {}
_DEFAULT_CONFIG_DIR: List[Optional[Path]] = []
_CACHE: Dict[Tuple[Optional[Path], Path], Tuple[tuple, AgentRegistry]] = {}
_LOCK = threading.Lock()


def _key(config_dir: Optional[Path]) -> Optional[Path]:
    return Path(config_dir).resolve() if config_dir is not None else None


def _stamp(path: Optional[Path]) -> tuple:
    try:
        stat = Path(path).stat() if path is not None else None
    except OSError:
        return (None,)
    return (stat.st_mtime_ns, stat.st_size) if stat else (None,)


def configure_custom_agents(config_dir: Optional[Path], settings_path: Path) -> None:
    """Let registries for `config_dir` include roles saved in `settings_path`.

    Lookups re-read the file when it changes, so roles added from the
    settings UI apply to the next mission without restarting.
    """
    with _LOCK:
        key = _key(config_dir)
        _CUSTOM_SOURCES[key] = Path(settings_path)
        _DEFAULT_CONFIG_DIR[:] = [key]


def get_agent_registry(config_dir: Optional[Path] = None) -> AgentRegistry:
    """Committed roles plus the user's custom roles for this app.

    Without a configured custom-role source this keeps the old behaviour:
    an explicit `config_dir` is read fresh and the no-argument form shares
    one built-in instance.
    """
    global _GLOBAL_REGISTRY
    if config_dir is None and _DEFAULT_CONFIG_DIR:
        key = _DEFAULT_CONFIG_DIR[0]
    else:
        key = _key(config_dir)
    source = _CUSTOM_SOURCES.get(key)
    if source is None:
        if config_dir is not None:
            return AgentRegistry.from_config(config_dir)
        if _GLOBAL_REGISTRY is None:
            _GLOBAL_REGISTRY = AgentRegistry.from_config()
        return _GLOBAL_REGISTRY
    stamp = (_stamp(key / "agents.json" if key is not None else None), _stamp(source))
    with _LOCK:
        cached = _CACHE.get((key, source))
        if cached is not None and cached[0] == stamp:
            return cached[1]
    registry = AgentRegistry.from_config(key).with_custom(load_custom_agents(source))
    with _LOCK:
        _CACHE[(key, source)] = (stamp, registry)
    return registry


def reset_agent_registry() -> None:
    global _GLOBAL_REGISTRY
    with _LOCK:
        _GLOBAL_REGISTRY = None
        _CUSTOM_SOURCES.clear()
        _DEFAULT_CONFIG_DIR.clear()
        _CACHE.clear()
