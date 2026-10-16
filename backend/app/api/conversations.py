import logging
import re
import threading
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.app.adapters.base import CLIAdapter
from backend.app.api.mission_detail import (
    build_mission_detail, _mission_payload, _derive_thoughts, _derive_tool_events,
)
from backend.app.domain.agent_registry import get_agent_registry
from backend.app.domain.models import (
    Conversation,
    ConversationStatus,
    MissionStatus,
    is_provider_verification_mission,
    utc_now,
)
from backend.app.settings import Settings, snapshot_settings
from backend.app.storage.repository import Repository

from backend.app.api.mission_requests import MissionRequest as CreateConversationMissionRequest, create_requested_mission
from backend.app.orchestration.roundtable import Roundtable, DiscussionError, ACTIVE

logger = logging.getLogger(__name__)


def _valid_agent_ids() -> set[str]:
    return set(get_agent_registry().ids())


class CreateConversationRequest(BaseModel):
    title: Optional[str] = None


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None
    status: Optional[ConversationStatus] = None


# 缺省占位标题:满足这些时才用首个 mission 的 goal 派生语义标题,
# 用户显式设置过的标题不动。
_DEFAULT_TITLES = {"", "新对话"}
# 句子切分:中英文常见的句末标点 + 换行
_SENTENCE_SPLIT = re.compile(r"[。！？!?\n]")
_TITLE_MAX_LEN = 24
# 起标题时识别开头点名 token 的前缀字符(展示用文本剥离,非路由)。
_MENTION_PREFIX = "@"


def _strip_leading_mentions(text: str) -> str:
    """剥掉开头连续的点名 token(以空白分隔)。仅用于显示派生,不参与路由。"""
    tokens = text.split()
    cursor = 0
    while cursor < len(tokens) and tokens[cursor][:1] == _MENTION_PREFIX:
        cursor += 1
    return " ".join(tokens[cursor:])


def _derive_conversation_title(goal: str) -> str:
    """从 mission goal 提炼语义标题:剥掉开头点名 token,取头一句,过长截断。"""
    text = _strip_leading_mentions((goal or "").strip()).strip()
    if not text:
        return "新对话"
    first = _SENTENCE_SPLIT.split(text, maxsplit=1)[0].strip() or text
    if len(first) > _TITLE_MAX_LEN:
        first = first[:_TITLE_MAX_LEN].rstrip() + "…"
    return first or "新对话"


def _conversation_summary(repo: Repository, conversation: Conversation) -> dict:
    all_missions = repo.list_missions_for_conversation(conversation.id)
    verification_ids = {
        mission.id for mission in all_missions if is_provider_verification_mission(mission)
    }
    missions = [mission for mission in all_missions if mission.id not in verification_ids]
    history = repo.read_message_history(conversation_id=conversation.id)
    messages = [entry.message for entry in history.project()]
    latest_mission_id = missions[-1].id if missions else None
    preview = messages[-1].content[:80] if messages else None
    payload = conversation.model_dump(mode="json")
    payload["latest_mission_id"] = latest_mission_id
    payload["latest_message_preview"] = preview
    payload["mission_count"] = len(missions)
    # 标题仍是缺省占位时,用首个 mission 的 goal 派生语义标题,让左侧列表彼此可辨。
    if (conversation.title or "").strip() in _DEFAULT_TITLES and missions:
        payload["title"] = _derive_conversation_title(missions[0].goal)
    return payload


def _conversation_detail(repo: Repository, settings: Settings, conversation: Conversation) -> dict:
    all_missions = repo.list_missions_for_conversation(conversation.id)
    verification_missions = [
        mission for mission in all_missions if is_provider_verification_mission(mission)
    ]
    verification_ids = {mission.id for mission in verification_missions}
    missions = [mission for mission in all_missions if mission.id not in verification_ids]
    history = repo.read_message_history(conversation_id=conversation.id)
    messages = [entry.message for entry in history.project()]
    from backend.app.api.mission_detail import _message_event
    message_events = [_message_event(m) for m in messages]
    latest_detail = None
    active_verification = next(
        (
            mission
            for mission in reversed(verification_missions)
            if mission.status not in {
                MissionStatus.BLOCKED,
                MissionStatus.COMPLETED,
                MissionStatus.COMPLETED_WITH_WARNINGS,
                MissionStatus.FAILED,
            }
        ),
        None,
    )
    latest_mission = active_verification or (missions[-1] if missions else None)
    if latest_mission is not None:
        latest_detail = build_mission_detail(repo, settings, latest_mission.id)
    history_events = [
        event
        for mission in missions
        for event in repo.list_events_after(mission.id, 0)
    ]
    return {
        "conversation": conversation.model_dump(mode="json"),
        "missions": [_mission_payload(m) for m in missions],
        "messages": message_events,
        "latest_mission_detail": latest_detail,
        "thoughts": _derive_thoughts(history_events),
        "tool_events": _derive_tool_events(history_events),
    }


def _run_mission_in_background(repo: Repository, settings: Settings, mission_id: str, adapter: Optional[CLIAdapter] = None) -> None:
    from backend.app.api.missions import _build_mission_adapter
    from backend.app.orchestration.runner import MissionRunner
    from backend.app.domain.models import MissionStatus

    try:
        MissionRunner(repo, settings, adapter or _build_mission_adapter(settings)).run(mission_id)
    except Exception as exc:
        logger.exception("Mission runner failed for %s", mission_id)
        mission = repo.get_mission(mission_id)
        if mission is None:
            return
        if mission.room_mode == 'roundtable':
            Roundtable(repo).fail(mission_id)
            return
        failed = mission.model_copy(update={"status": MissionStatus.FAILED, "updated_at": utc_now()})
        repo.update_mission(failed)
        repo.add_event(
            mission_id,
            "status",
            {"mission_id": mission_id, "status": MissionStatus.FAILED.value,
             "summary": "Mission runner failed: {e}".format(e=exc)},
        )


def build_router(settings: Settings, repository: Repository, adapter_override: Optional[CLIAdapter] = None) -> APIRouter:
    router = APIRouter(prefix="/api/conversations", tags=["conversations"])

    def get_repo() -> Repository:
        return repository

    @router.post("", status_code=201)
    def create_conversation(
        payload: CreateConversationRequest,
        repo: Repository = Depends(get_repo),
    ):
        conversation = Conversation(
            id="c_{suffix}".format(suffix=uuid4().hex[:12]),
            title=payload.title or "新对话",
        )
        repo.create_conversation(conversation)
        return conversation.model_dump(mode="json")

    @router.get("")
    def list_conversations(
        status: ConversationStatus = Query(default=ConversationStatus.ACTIVE),
        repo: Repository = Depends(get_repo),
    ):
        conversations = [
            conversation
            for conversation in repo.list_conversations()
            if conversation.status == status
        ]
        return [_conversation_summary(repo, c) for c in conversations]

    @router.get("/{conversation_id}")
    def get_conversation(conversation_id: str, repo: Repository = Depends(get_repo)):
        conversation = repo.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return _conversation_detail(repo, settings, conversation)

    @router.patch("/{conversation_id}")
    def update_conversation(
        conversation_id: str,
        payload: UpdateConversationRequest,
        repo: Repository = Depends(get_repo),
    ):
        conversation = repo.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        update_data = payload.model_dump(exclude_unset=True)
        if not update_data:
            return _conversation_detail(repo, settings, conversation)

        with repo._transaction(immediate=True):
            conversation = repo.get_conversation(conversation_id)
            if update_data.get('status') == ConversationStatus.ARCHIVED:
                if any(m.status in ACTIVE for m in repo.list_missions_for_conversation(conversation_id)):
                    raise DiscussionError('Stop the active mission before archiving')
            updated = conversation.model_copy(update={**update_data, 'updated_at': utc_now()})
            repo.update_conversation(updated)
        return _conversation_detail(repo, settings, updated)

    @router.post("/{conversation_id}/missions", status_code=201)
    def create_conversation_mission(
        conversation_id: str,
        payload: CreateConversationMissionRequest,
        repo: Repository = Depends(get_repo),
    ):
        mission_settings = snapshot_settings(settings)
        mission, conversation, created = create_requested_mission(repo, mission_settings, payload, conversation_id)
        if created:
            threading.Thread(target=_run_mission_in_background, args=(repo, mission_settings, mission.id, adapter_override), daemon=True).start()
        return _conversation_detail(repo, settings, conversation)

    return router
