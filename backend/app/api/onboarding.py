from __future__ import annotations

import threading
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from backend.app.adapters.fake import FakeCLIAdapter
from backend.app.adapters.base import CLIAdapter, TurnOutcome
from backend.app.api.conversations import _conversation_detail
from backend.app.api.missions import _run_mission_in_background
from backend.app.config.providers import (
    ProviderNotFoundError,
    diagnose_provider,
    load_cli_providers,
)
from backend.app.domain.models import (
    Conversation,
    Message,
    MessageRole,
    Mission,
    MissionKind,
    MissionStatus,
    TurnStatus,
    utc_now,
)
from backend.app.mcp.tools import MCPTools
from backend.app.settings import AgentOverride, ProviderOverride, Settings
from backend.app.storage import hub_settings
from backend.app.storage.repository import Repository


GUIDE_AGENT_ID = "chulan"
ONBOARDING_TITLE = "欢迎使用 AgentClub"
ONBOARDING_GOAL = (
    "@张楚岚 我刚启动 AgentClub，请用两三句话介绍你自己，并带我完成初始配置。"
)
ONBOARDING_REPLY = (
    "哥，听得见，AgentClub 这边已经跑通了。我是张楚岚，先负责带你熟悉这个聊天室。"
    "现在用的是不会产生模型费用的演示 CLI；你可以先随便聊两句，之后再接入真实 CLI。"
)
PROVIDER_PRIORITY = ("codex", "claude", "antigravity")
PROVIDER_HELP = {
    "codex": {
        "install_command": "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
        "install_url": "https://learn.chatgpt.com/docs/codex/cli",
        "auth_command": "codex login",
    },
    "claude": {
        "install_command": "curl -fsSL https://claude.ai/install.sh | bash",
        "install_url": "https://code.claude.com/docs/en/installation",
        "auth_command": "claude auth login",
    },
    "antigravity": {
        "install_command": "curl -fsSL https://antigravity.google/cli/install.sh | bash",
        "install_url": "https://antigravity.google/docs/cli/install",
        "auth_command": "agy",
    },
}
_TERMINAL_STATUSES = {
    MissionStatus.BLOCKED,
    MissionStatus.COMPLETED,
    MissionStatus.COMPLETED_WITH_WARNINGS,
    MissionStatus.FAILED,
}
TEAM_AGENT_IDS = ("baobao", "wangye")
TEAM_INTRO_GOAL = (
    "@冯宝宝 @王也 张楚岚已经接入 {provider_label}。请你们各用一句话向新用户介绍自己，"
    "说说自己的性格和交流习惯，不介绍固定分工。"
)
TEAM_INTRO_REPLIES = {
    "baobao": "我是冯宝宝。有啥子就直说，不懂的我会问，想明白了也会告诉你。",
    "wangye": "王也。慢慢聊，我有自己的想法，也愿意听能让我改主意的道理。",
}


class _TeamIntroAdapter(CLIAdapter):
    """Deterministic onboarding replies; never invokes a real provider."""

    def run_turn(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        prompt: str,
        tools: MCPTools,
    ) -> TurnOutcome:
        content = TEAM_INTRO_REPLIES.get(
            agent_id,
            "我已经加入聊天室，很高兴和你一起聊聊、解决问题。",
        )
        tools.publish_message(
            mission_id,
            agent_id,
            turn_id,
            content,
            provider_id,
        )
        if agent_id == "baobao":
            tools.invoke_agent(
                mission_id,
                sender_agent_id=agent_id,
                sender_turn_id=turn_id,
                target_agent_id="wangye",
                purpose="向新用户介绍你的性格和交流习惯",
                provider_id=provider_id,
            )
        return TurnOutcome(status=TurnStatus.COMPLETED)


class UpdateOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed: Optional[bool] = None
    dismissed_real_cli_setup: Optional[bool] = None


class VerifyProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_real_call: bool


def build_router(
    settings: Settings,
    repository: Repository,
    verification_adapter: Optional[CLIAdapter] = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])
    state_lock = threading.Lock()

    @router.get("")
    def get_onboarding():
        with state_lock:
            return _build_state(repository, settings, persist=True)

    @router.post("/first-chat", status_code=202)
    def start_first_chat():
        with state_lock:
            state = _build_state(repository, settings, persist=True)
            conversation = _get_state_conversation(repository, state)
            mission = _get_state_mission(repository, state)

            if state["state"] in {
                "first_chat_running",
                "first_chat_succeeded",
                "provider_verifying",
                "provider_verification_failed",
                "provider_verified",
                "team_intro_running",
                "team_intro_failed",
                "team_intro_completed",
                "completed",
            } and conversation is not None and mission is not None:
                return {
                    "onboarding": state,
                    "conversation": _conversation_detail(repository, settings, conversation),
                }

            if conversation is None:
                conversation = Conversation(
                    id="c_{suffix}".format(suffix=uuid4().hex[:12]),
                    title=ONBOARDING_TITLE,
                )
                repository.create_conversation(conversation)

            mission = Mission(
                id="m_{suffix}".format(suffix=uuid4().hex),
                title=ONBOARDING_TITLE,
                goal=ONBOARDING_GOAL,
                conversation_id=conversation.id,
                target_agent_id=GUIDE_AGENT_ID,
                target_agent_ids=[GUIDE_AGENT_ID],
            )
            repository.create_mission(mission)
            repository.add_message(
                Message(
                    id="msg_{suffix}".format(suffix=uuid4().hex),
                    mission_id=mission.id,
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    content=ONBOARDING_GOAL,
                )
            )
            conversation = conversation.model_copy(update={"updated_at": utc_now()})
            repository.update_conversation(conversation)

            data = _load_file(settings)
            onboarding = _onboarding_section(data)
            onboarding.update(
                {
                    "guide_agent_id": GUIDE_AGENT_ID,
                    "conversation_id": conversation.id,
                    "mission_id": mission.id,
                    "first_reply_completed_at": None,
                    "completed_at": None,
                    "dismissed_real_cli_setup": False,
                }
            )
            _save_file(settings, data)

            fake_settings = settings.model_copy(
                update={
                    "use_fake_cli": True,
                    "max_turns": 1,
                    "max_a2a_invocations": 0,
                    "provider_overrides": {},
                    "agent_overrides": {
                        GUIDE_AGENT_ID: AgentOverride(
                            default_provider_id="fake",
                            fallback_provider_ids=[],
                        )
                    },
                }
            )
            threading.Thread(
                target=_run_mission_in_background,
                args=(
                    repository,
                    fake_settings,
                    mission.id,
                    FakeCLIAdapter(response=ONBOARDING_REPLY),
                ),
                daemon=True,
            ).start()

            return {
                "onboarding": _build_state(repository, settings, persist=False),
                "conversation": _conversation_detail(repository, settings, conversation),
            }

    @router.patch("")
    def update_onboarding(payload: UpdateOnboardingRequest):
        with state_lock:
            data = _load_file(settings)
            onboarding = _onboarding_section(data)
            if payload.completed is not None:
                onboarding["completed_at"] = (
                    utc_now().isoformat() if payload.completed else None
                )
            if payload.dismissed_real_cli_setup is not None:
                onboarding["dismissed_real_cli_setup"] = payload.dismissed_real_cli_setup
            _save_file(settings, data)
            return _build_state(repository, settings, persist=True)

    @router.post("/providers/{provider_id}/verify", status_code=202)
    def verify_provider(provider_id: str, payload: VerifyProviderRequest):
        if not payload.confirmed_real_call:
            raise HTTPException(
                status_code=400,
                detail="必须明确确认后才能发起一次真实 CLI 请求",
            )

        with state_lock:
            provider = _get_onboarding_provider(settings, provider_id)
            state = _build_state(repository, settings, persist=True)
            if not state.get("first_reply_completed_at"):
                raise HTTPException(status_code=409, detail="请先完成 Fake 首次对话")

            existing_id = state.get("verification_mission_id")
            existing = repository.get_mission(existing_id) if existing_id else None
            current_provider_id = state.get("verification_provider_id")
            if (
                state["state"] == "provider_verifying"
                and existing
                and current_provider_id != provider_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="另一个真实 CLI 正在验证，请等待它结束",
                )
            if (
                state["state"] in {"provider_verifying", "provider_verified"}
                and existing
                and current_provider_id == provider_id
            ):
                conversation = _get_state_conversation(repository, state)
                if conversation is None:
                    raise HTTPException(status_code=409, detail="启动引导会话不存在")
                return {
                    "onboarding": state,
                    "conversation": _conversation_detail(repository, settings, conversation),
                }

            readiness = _provider_readiness(settings, provider_id)
            if not readiness["available"]:
                raise HTTPException(
                    status_code=409,
                    detail=readiness.get("error") or "未找到 {label}".format(label=provider.label),
                )

            conversation = _get_state_conversation(repository, state)
            if conversation is None:
                raise HTTPException(status_code=409, detail="启动引导会话不存在")

            mission = Mission(
                id="m_{suffix}".format(suffix=uuid4().hex),
                title="验证 {label} 接入".format(label=provider.label),
                goal=_verification_goal(provider.label),
                kind=MissionKind.PROVIDER_VERIFICATION,
                conversation_id=conversation.id,
                target_agent_id=GUIDE_AGENT_ID,
                target_agent_ids=[GUIDE_AGENT_ID],
            )
            repository.create_mission(mission)
            conversation = conversation.model_copy(update={"updated_at": utc_now()})
            repository.update_conversation(conversation)

            data = _load_file(settings)
            onboarding = _onboarding_section(data)
            onboarding.update(
                {
                    "verification_provider_id": provider_id,
                    "verification_mission_id": mission.id,
                    "verification_started_at": utc_now().isoformat(),
                    "verification_error": None,
                }
            )
            # A verification started from the persistent team settings should
            # temporarily reopen the setup card without resetting the original
            # welcome conversation or the already configured team.
            onboarding.pop("completed_at", None)
            _save_file(settings, data)

            verification_settings = settings.model_copy(
                update={
                    "use_fake_cli": False,
                    "max_turns": 1,
                    "max_a2a_invocations": 0,
                    "provider_overrides": {
                        provider_id: ProviderOverride(enabled=True),
                    },
                    "agent_overrides": {
                        GUIDE_AGENT_ID: AgentOverride(
                            default_provider_id=provider_id,
                            fallback_provider_ids=[],
                        )
                    },
                }
            )
            threading.Thread(
                target=_run_mission_in_background,
                args=(
                    repository,
                    verification_settings,
                    mission.id,
                    verification_adapter,
                ),
                daemon=True,
            ).start()

            return {
                "onboarding": _build_state(repository, settings, persist=False),
                "conversation": _conversation_detail(repository, settings, conversation),
            }

    @router.post("/providers/{provider_id}/activate", status_code=202)
    def activate_provider(provider_id: str):
        with state_lock:
            provider = _get_onboarding_provider(settings, provider_id)
            state = _build_state(repository, settings, persist=True)
            if provider_id not in state["verified_provider_ids"]:
                raise HTTPException(status_code=409, detail="该 CLI 尚未通过 MCP 发言验证")

            conversation = _get_state_conversation(repository, state)
            if conversation is None:
                raise HTTPException(status_code=409, detail="启动引导会话不存在")

            existing_intro_id = state.get("team_intro_mission_id")
            existing_intro = (
                repository.get_mission(existing_intro_id)
                if existing_intro_id
                else None
            )
            if existing_intro is not None and state["state"] in {
                "team_intro_running",
                "team_intro_completed",
                "completed",
            }:
                return {
                    "onboarding": state,
                    "conversation": _conversation_detail(repository, settings, conversation),
                }

            data = _load_file(settings)
            runtime = data.setdefault("runtime", {})
            providers = data.setdefault("providers", {})
            agents = data.setdefault("agents", {})
            onboarding = _onboarding_section(data)

            runtime["use_fake_cli"] = False
            providers.setdefault("fake", {})["enabled"] = True
            providers.setdefault(provider_id, {})["enabled"] = True
            agents[GUIDE_AGENT_ID] = {
                "default_provider_id": provider_id,
                "fallback_provider_ids": ["fake"],
            }
            agents["baobao"] = {
                "default_provider_id": "fake",
                "fallback_provider_ids": [],
            }
            agents["wangye"] = {
                "default_provider_id": "fake",
                "fallback_provider_ids": [],
            }
            activated_at = utc_now().isoformat()
            onboarding["activated_provider_id"] = provider_id
            onboarding["provider_activated_at"] = activated_at
            onboarding.pop("completed_at", None)
            onboarding["dismissed_real_cli_setup"] = False
            intro_goal = TEAM_INTRO_GOAL.format(provider_label=provider.label)

            intro_mission = Mission(
                id="m_{suffix}".format(suffix=uuid4().hex),
                title="认识宝儿姐和王也",
                goal=intro_goal,
                conversation_id=conversation.id,
                target_agent_id=TEAM_AGENT_IDS[0],
                target_agent_ids=[TEAM_AGENT_IDS[0]],
            )
            repository.create_mission(intro_mission)
            repository.add_message(
                Message(
                    id="msg_{suffix}".format(suffix=uuid4().hex),
                    mission_id=intro_mission.id,
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    content=intro_goal,
                )
            )
            conversation = conversation.model_copy(update={"updated_at": utc_now()})
            repository.update_conversation(conversation)
            onboarding["team_intro_mission_id"] = intro_mission.id
            onboarding["team_intro_started_at"] = utc_now().isoformat()
            onboarding.pop("team_intro_completed_at", None)
            _save_file(settings, data)

            intro_settings = settings.model_copy(
                update={
                    "use_fake_cli": True,
                    "max_turns": 1,
                    "max_a2a_invocations": 1,
                    "provider_overrides": {},
                    "agent_overrides": {
                        agent_id: AgentOverride(
                            default_provider_id="fake",
                            fallback_provider_ids=[],
                        )
                        for agent_id in TEAM_AGENT_IDS
                    },
                }
            )
            threading.Thread(
                target=_run_mission_in_background,
                args=(
                    repository,
                    intro_settings,
                    intro_mission.id,
                    _TeamIntroAdapter(),
                ),
                daemon=True,
            ).start()
            return {
                "onboarding": _build_state(repository, settings, persist=False),
                "conversation": _conversation_detail(repository, settings, conversation),
            }

    @router.post("/reset")
    def reset_onboarding():
        with state_lock:
            data = _load_file(settings)
            previous = data.get("onboarding")
            verified = previous.get("verified_providers", {}) if isinstance(previous, dict) else {}
            data["onboarding"] = {
                "guide_agent_id": GUIDE_AGENT_ID,
                "verified_providers": verified,
                "dismissed_real_cli_setup": False,
                "completed_at": None,
            }
            _save_file(settings, data)
            return _build_state(repository, settings, persist=False)

    return router


def _build_state(
    repository: Repository,
    settings: Settings,
    *,
    persist: bool,
) -> dict:
    data = _load_file(settings)
    onboarding = _onboarding_section(data)
    changed = False

    conversation_id = _optional_string(onboarding.get("conversation_id"))
    mission_id = _optional_string(onboarding.get("mission_id"))
    conversation = repository.get_conversation(conversation_id) if conversation_id else None
    mission = repository.get_mission(mission_id) if mission_id else None

    if conversation_id and conversation is None:
        onboarding.pop("conversation_id", None)
        conversation_id = None
        changed = True
    if mission_id and mission is None:
        onboarding.pop("mission_id", None)
        mission_id = None
        changed = True

    first_reply_completed_at = _optional_string(
        onboarding.get("first_reply_completed_at")
    )
    if mission is not None and first_reply_completed_at is None:
        reply = next(
            (
                message
                for message in repository.list_messages(mission.id)
                if message.role == MessageRole.AGENT
                and message.agent_id == GUIDE_AGENT_ID
            ),
            None,
        )
        if reply is not None:
            first_reply_completed_at = reply.created_at.isoformat()
            onboarding["first_reply_completed_at"] = first_reply_completed_at
            changed = True

    verification_provider_id = _optional_string(
        onboarding.get("verification_provider_id")
    ) or (
        "claude" if _optional_string(onboarding.get("claude_verification_mission_id")) else None
    )
    verification_mission_id = _optional_string(
        onboarding.get("verification_mission_id")
    ) or _optional_string(onboarding.get("claude_verification_mission_id"))
    verification_mission = (
        repository.get_mission(verification_mission_id)
        if verification_mission_id
        else None
    )
    if verification_mission_id and verification_mission is None:
        onboarding.pop("verification_mission_id", None)
        onboarding.pop("verification_provider_id", None)
        verification_provider_id = None
        verification_mission_id = None
        changed = True

    team_intro_mission_id = _optional_string(
        onboarding.get("team_intro_mission_id")
    )
    team_intro_mission = (
        repository.get_mission(team_intro_mission_id)
        if team_intro_mission_id
        else None
    )
    if team_intro_mission_id and team_intro_mission is None:
        for key in (
            "team_intro_mission_id",
            "team_intro_started_at",
            "team_intro_completed_at",
        ):
            onboarding.pop(key, None)
        team_intro_mission_id = None
        changed = True

    verified = onboarding.get("verified_providers")
    if not isinstance(verified, dict):
        verified = {}
        onboarding["verified_providers"] = verified
        changed = True

    verification_succeeded = False
    if verification_mission is not None and verification_mission.status in {
        MissionStatus.COMPLETED,
        MissionStatus.COMPLETED_WITH_WARNINGS,
    }:
        verification_reply = next(
            (
                message
                for message in repository.list_messages(verification_mission.id)
                if message.role == MessageRole.AGENT
                and message.agent_id == GUIDE_AGENT_ID
                and message.provider_id == verification_provider_id
            ),
            None,
        )
        if verification_reply is not None:
            verification_succeeded = True
            if verification_provider_id and verification_provider_id not in verified:
                readiness = _provider_readiness(settings, verification_provider_id)
                verified[verification_provider_id] = {
                    "verified_at": verification_reply.created_at.isoformat(),
                    "version": readiness.get("version"),
                    "method": "mcp_publish_message",
                }
                onboarding["verification_error"] = None
                changed = True

    verification_failed = (
        verification_mission is not None
        and verification_mission.status in _TERMINAL_STATUSES
        and not verification_succeeded
    )
    if verification_failed:
        failure = _mission_failure_summary(repository, verification_mission.id)
        if onboarding.get("verification_error") != failure:
            onboarding["verification_error"] = failure
            changed = True

    team_intro_succeeded = False
    if team_intro_mission is not None and team_intro_mission.status in {
        MissionStatus.COMPLETED,
        MissionStatus.COMPLETED_WITH_WARNINGS,
    }:
        intro_replies = {
            message.agent_id: message
            for message in repository.list_messages(team_intro_mission.id)
            if message.role == MessageRole.AGENT
            and message.agent_id in TEAM_AGENT_IDS
        }
        team_intro_succeeded = all(
            agent_id in intro_replies for agent_id in TEAM_AGENT_IDS
        )
        if team_intro_succeeded and not _optional_string(
            onboarding.get("team_intro_completed_at")
        ):
            onboarding["team_intro_completed_at"] = max(
                intro_replies[agent_id].created_at for agent_id in TEAM_AGENT_IDS
            ).isoformat()
            changed = True

    team_intro_failed = (
        team_intro_mission is not None
        and team_intro_mission.status in _TERMINAL_STATUSES
        and not team_intro_succeeded
    )

    activated_provider_id = _optional_string(
        onboarding.get("activated_provider_id")
    )
    is_extension_verification = bool(
        activated_provider_id
        and verification_provider_id
        and verification_provider_id != activated_provider_id
    )
    completed_at = _optional_string(onboarding.get("completed_at"))
    if completed_at:
        state = "completed"
    elif is_extension_verification and verification_succeeded:
        state = "provider_verified"
    elif (
        is_extension_verification
        and verification_mission is not None
        and verification_mission.status not in _TERMINAL_STATUSES
    ):
        state = "provider_verifying"
    elif is_extension_verification and verification_failed:
        state = "provider_verification_failed"
    elif team_intro_succeeded:
        state = "team_intro_completed"
    elif team_intro_mission is not None and team_intro_mission.status not in _TERMINAL_STATUSES:
        state = "team_intro_running"
    elif team_intro_failed:
        state = "team_intro_failed"
    elif verification_succeeded:
        state = "provider_verified"
    elif verification_mission is not None and verification_mission.status not in _TERMINAL_STATUSES:
        state = "provider_verifying"
    elif verification_failed:
        state = "provider_verification_failed"
    elif first_reply_completed_at:
        state = "first_chat_succeeded"
    elif mission is not None and mission.status not in _TERMINAL_STATUSES:
        state = "first_chat_running"
    elif mission is not None:
        state = "first_chat_failed"
    else:
        state = "ready_for_first_chat"

    if changed and persist:
        _save_file(settings, data)

    verified_provider_ids = sorted(verified)
    provider_views = _provider_views(
        settings,
        verified,
        activated_provider_id=activated_provider_id,
        diagnose=bool(
            first_reply_completed_at
            and not completed_at
        ),
    )
    recommended_provider_id = _recommended_provider_id(provider_views)
    return {
        "state": state,
        "guide_agent_id": GUIDE_AGENT_ID,
        "conversation_id": conversation_id,
        "mission_id": mission_id,
        "verification_provider_id": verification_provider_id,
        "verification_mission_id": verification_mission_id,
        "team_intro_mission_id": team_intro_mission_id,
        "first_reply_completed_at": first_reply_completed_at,
        "team_intro_completed_at": _optional_string(
            onboarding.get("team_intro_completed_at")
        ),
        "completed_at": completed_at,
        "activated_provider_id": activated_provider_id,
        "verified_provider_ids": verified_provider_ids,
        "providers": provider_views,
        "recommended_provider_id": recommended_provider_id,
        "verification_error": _optional_string(onboarding.get("verification_error")),
        "dismissed_real_cli_setup": bool(
            onboarding.get("dismissed_real_cli_setup", False)
        ),
    }


def _get_state_conversation(
    repository: Repository,
    state: dict,
) -> Optional[Conversation]:
    conversation_id = state.get("conversation_id")
    return repository.get_conversation(conversation_id) if conversation_id else None


def _get_state_mission(repository: Repository, state: dict) -> Optional[Mission]:
    mission_id = state.get("mission_id")
    return repository.get_mission(mission_id) if mission_id else None


def _load_file(settings: Settings) -> dict:
    try:
        return hub_settings.load(hub_settings.default_path(settings))
    except hub_settings.HubSettingsError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _save_file(settings: Settings, data: dict) -> None:
    data.setdefault("schema_version", 1)
    hub_settings.save(hub_settings.default_path(settings), data)


def _onboarding_section(data: dict) -> dict:
    section = data.get("onboarding")
    if not isinstance(section, dict):
        section = {}
        data["onboarding"] = section
    section.setdefault("guide_agent_id", GUIDE_AGENT_ID)
    return section


def _optional_string(value: object) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _get_onboarding_provider(settings: Settings, provider_id: str):
    for provider in load_cli_providers(settings.config_dir):
        if provider.id == provider_id and provider.id != "fake" and provider.mcp_enabled:
            return provider
    raise HTTPException(status_code=404, detail="不支持的 onboarding Provider")


def _provider_readiness(settings: Settings, provider_id: str) -> dict:
    try:
        result = diagnose_provider(
            settings.config_dir,
            provider_id,
            overrides={provider_id: ProviderOverride(enabled=True)},
        )
    except ProviderNotFoundError as exc:
        return {
            "available": False,
            "version": None,
            "path": None,
            "error": str(exc),
        }
    return {
        "available": bool(result["available"] and not result.get("error")),
        "version": result.get("version"),
        "path": result.get("path"),
        "error": result.get("error"),
    }


def _provider_views(
    settings: Settings,
    verified: dict,
    *,
    activated_provider_id: Optional[str],
    diagnose: bool,
) -> list[dict]:
    configured = {
        provider.id: provider
        for provider in load_cli_providers(settings.config_dir)
        if provider.id != "fake" and provider.mcp_enabled
    }
    ordered_ids = [pid for pid in PROVIDER_PRIORITY if pid in configured]
    ordered_ids.extend(pid for pid in configured if pid not in ordered_ids)
    views = []
    for provider_id in ordered_ids:
        provider = configured[provider_id]
        readiness = (
            _provider_readiness(settings, provider_id)
            if diagnose
            else {
                "available": provider_id in verified,
                "version": verified.get(provider_id, {}).get("version"),
                "path": None,
                "error": None,
            }
        )
        help_data = PROVIDER_HELP.get(provider_id, {})
        views.append(
            {
                "id": provider_id,
                "label": provider.label,
                **readiness,
                "verified": provider_id in verified,
                "activated": provider_id == activated_provider_id,
                "install_command": help_data.get("install_command"),
                "install_url": help_data.get("install_url"),
                "auth_command": help_data.get("auth_command"),
            }
        )
    return views


def _recommended_provider_id(provider_views: list[dict]) -> Optional[str]:
    verified = next(
        (item for item in provider_views if item["verified"] and item["available"]),
        None,
    )
    if verified:
        return verified["id"]
    available = next((item for item in provider_views if item["available"]), None)
    if available:
        return available["id"]
    return "codex" if any(item["id"] == "codex" for item in provider_views) else (
        provider_views[0]["id"] if provider_views else None
    )


def _verification_goal(provider_label: str) -> str:
    return (
        "@张楚岚 这是 {label} 的内部连接诊断。请只用一句简短中文发送“连接测试完成”，"
        "并调用 publish_message。不要声明当前 Provider 名称，不要介绍其他角色，也不要引用历史对话。"
    ).format(label=provider_label)


def _mission_failure_summary(repository: Repository, mission_id: str) -> str:
    for event in reversed(repository.list_events_after(mission_id, 0)):
        summary = event.payload.get("summary") or event.payload.get("error")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    return "CLI 已经运行，但没有通过 AgentClub 发布角色消息"
