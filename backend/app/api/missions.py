import asyncio
import json
import logging
import threading
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.adapters.base import CLIAdapter
from backend.app.adapters.fake import FakeCLIAdapter
from backend.app.adapters.provider_router import ProviderRouterAdapter
from backend.app.api.mission_detail import (
    TERMINAL_STATUSES,
    _artifact_records,
    _mission_payload,
    build_mission_detail,
    read_artifact_content,
)
from backend.app.api.conversations import _valid_agent_ids
from backend.app.domain.models import Conversation, Message, MessageRole, Mission, MissionStatus, utc_now
from backend.app.orchestration.runner import MissionRunner
from backend.app.settings import Settings, get_settings, snapshot_settings
from backend.app.storage.repository import Repository

logger = logging.getLogger(__name__)


class CreateMissionRequest(BaseModel):
    goal: str
    title: Optional[str] = None
    conversation_id: Optional[str] = None
    target_agent_id: Optional[str] = None
    target_agent_ids: Optional[List[str]] = None


class InterruptRequest(BaseModel):
    content: str
    target_agent_id: Optional[str] = None


def _resolve_target_ids(
    target_agent_ids: Optional[List[str]],
    target_agent_id: Optional[str],
) -> List[str]:
    """Normalize the two equivalent request fields. Plural wins if both given,
    so a multi-mention payload like `@A @B` is never silently downgraded."""
    if target_agent_ids:
        return list(target_agent_ids)
    if target_agent_id:
        return [target_agent_id]
    return []


_snapshot_settings = snapshot_settings  # backward-compatible alias for existing tests


def _build_mission_adapter(settings: Settings) -> CLIAdapter:
    if settings.use_fake_cli:
        return FakeCLIAdapter()
    return ProviderRouterAdapter(
        settings.config_dir,
        cli_timeout=settings.cli_timeout,
        a2a_idle_timeout=settings.a2a_idle_timeout,
        database_path=settings.database_path,
        artifact_root=settings.artifact_root,
        provider_overrides=settings.provider_overrides,
    )


def _run_mission_in_background(
    repo: Repository,
    settings: Settings,
    mission_id: str,
    adapter: Optional[CLIAdapter] = None,
) -> None:
    try:
        MissionRunner(repo, settings, adapter or _build_mission_adapter(settings)).run(mission_id)
    except Exception as exc:
        logger.exception("Mission runner failed for %s", mission_id)
        mission = repo.get_mission(mission_id)
        if mission is None:
            return
        failed = mission.model_copy(
            update={
                "status": MissionStatus.FAILED,
                "updated_at": utc_now(),
            }
        )
        repo.update_mission(failed)
        repo.add_event(
            mission_id,
            "status",
            {
                "mission_id": mission_id,
                "status": MissionStatus.FAILED.value,
                "summary": "Mission runner failed: {error}".format(error=exc),
            },
        )


def build_router(
    settings: Settings,
    repository: Repository,
    adapter_override: Optional[CLIAdapter] = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/missions", tags=["missions"])

    def get_repo() -> Repository:
        return repository

    @router.post("", status_code=201)
    def create_mission(payload: CreateMissionRequest, repo: Repository = Depends(get_repo)):
        target_ids = _resolve_target_ids(payload.target_agent_ids, payload.target_agent_id)
        valid_ids = _valid_agent_ids()
        invalid = [tid for tid in target_ids if tid not in valid_ids]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail="Invalid target_agent_id(s) {bad}: must be one of {ids}".format(
                    bad=", ".join(invalid),
                    ids=", ".join(sorted(valid_ids)),
                ),
            )

        conversation_id = payload.conversation_id
        if conversation_id is not None:
            if repo.get_conversation(conversation_id) is None:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            conv = Conversation(
                id="c_{suffix}".format(suffix=uuid4().hex[:12]),
                title=payload.title or payload.goal[:40],
            )
            repo.create_conversation(conv)
            conversation_id = conv.id

        mission = Mission(
            id="m_{suffix}".format(suffix=uuid4().hex),
            title=payload.title or payload.goal,
            goal=payload.goal,
            conversation_id=conversation_id,
            target_agent_id=target_ids[0] if target_ids else None,
            target_agent_ids=target_ids,
        )
        repo.create_mission(mission)
        repo.add_message(
            Message(
                id="msg_{suffix}".format(suffix=uuid4().hex),
                mission_id=mission.id,
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=payload.goal,
            )
        )

        detail = build_mission_detail(repo, settings, mission.id)
        mission_settings = snapshot_settings(settings)
        threading.Thread(
            target=_run_mission_in_background,
            args=(repo, mission_settings, mission.id, adapter_override),
            daemon=True,
        ).start()
        return detail

    @router.get("")
    def list_missions(repo: Repository = Depends(get_repo)):
        return [_mission_payload(mission) for mission in repo.list_missions()]

    @router.get("/{mission_id}")
    def get_mission(mission_id: str, repo: Repository = Depends(get_repo)):
        mission = repo.get_mission(mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        return build_mission_detail(repo, settings, mission.id)

    @router.post("/{mission_id}/interruptions", status_code=202)
    def add_interruption(
        mission_id: str,
        payload: InterruptRequest,
        repo: Repository = Depends(get_repo),
    ):
        if repo.get_mission(mission_id) is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        repo.add_interruption(mission_id, payload.content, payload.target_agent_id)
        return build_mission_detail(repo, settings, mission_id)

    @router.get("/{mission_id}/artifacts")
    def list_artifacts(mission_id: str, repo: Repository = Depends(get_repo)):
        if repo.get_mission(mission_id) is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        db_events = repo.list_events_after(mission_id, 0)
        return _artifact_records(settings, mission_id, db_events)

    @router.get("/{mission_id}/artifacts/content")
    def get_artifact_content(
        mission_id: str,
        path: str,
        repo: Repository = Depends(get_repo),
    ):
        if repo.get_mission(mission_id) is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        return read_artifact_content(settings, mission_id, path)

    @router.get("/{mission_id}/stream")
    def stream_mission(mission_id: str, repo: Repository = Depends(get_repo)):
        if repo.get_mission(mission_id) is None:
            raise HTTPException(status_code=404, detail="Mission not found")

        async def events():
            while True:
                detail = build_mission_detail(repo, settings, mission_id)
                snapshot = {"mission_id": mission_id, **detail}
                yield "event: snapshot\n"
                yield "data: {data}\n\n".format(
                    data=json.dumps(snapshot, ensure_ascii=False)
                )
                if detail["mission"]["status"] in TERMINAL_STATUSES:
                    break
                await asyncio.sleep(0.05)

        return StreamingResponse(events(), media_type="text/event-stream")

    return router
