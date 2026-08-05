import re
from typing import Dict, Optional

from backend.app.domain.agent_registry import AgentRegistry, get_agent_registry
from backend.app.domain.models import MessageRole, is_provider_verification_mission
from backend.app.storage.repository import Repository

_WHITESPACE_RUN = re.compile(r"\s+")


def build_conversation_context(
    repository: Repository,
    conversation_id: Optional[str],
    *,
    limit: int = 8,
    max_chars_per_message: int = 220,
    agent_registry: Optional[AgentRegistry] = None,
) -> str:
    if not conversation_id:
        return ""
    verification_mission_ids = {
        mission.id
        for mission in repository.list_missions_for_conversation(conversation_id)
        if is_provider_verification_mission(mission)
    }
    messages = [
        message
        for message in repository.list_messages_for_conversation(conversation_id)
        if message.mission_id not in verification_mission_ids
    ]
    if not messages:
        return ""
    registry = agent_registry or get_agent_registry()
    recent = messages[-limit:]
    lines = []
    for msg in recent:
        speaker = _speaker_label(msg.role, msg.agent_id, registry)
        text = _WHITESPACE_RUN.sub(" ", msg.content).strip()
        if len(text) > max_chars_per_message:
            text = text[:max_chars_per_message] + "…"
        lines.append("{speaker}：{text}".format(speaker=speaker, text=text))
    return "最近对话：\n" + "\n".join(lines)


def build_artifact_index(
    repository: Repository,
    mission_id: str,
    *,
    limit: int = 12,
) -> str:
    events = repository.list_events_after(mission_id, 0)
    latest: Dict[str, dict] = {}
    for ev in events:
        if ev.event_type != "artifact":
            continue
        path = ev.payload.get("path")
        if not isinstance(path, str) or not path:
            continue
        latest[path] = {
            "path": path,
            "agent_id": ev.payload.get("agent_id") or "?",
            "title": ev.payload.get("title") or path,
            "created_at": ev.created_at,
        }
    if not latest:
        return ""
    entries = sorted(
        latest.values(), key=lambda e: e["created_at"], reverse=True
    )[:limit]
    lines = [
        "【产物清单】（要引用别人写的内容时，先用 read_artifact 读取，不要凭印象引述）：",
    ]
    for entry in entries:
        lines.append(
            "- {path}  by {agent}（{title}）".format(
                path=entry["path"],
                agent=entry["agent_id"],
                title=entry["title"],
            )
        )
    return "\n".join(lines)


def _speaker_label(
    role: MessageRole,
    agent_id: Optional[str],
    registry: AgentRegistry,
) -> str:
    if role == MessageRole.USER:
        return "用户"
    if role == MessageRole.SYSTEM:
        return "系统"
    return registry.display_name(agent_id)
