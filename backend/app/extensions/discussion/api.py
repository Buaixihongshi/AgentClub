"""Discussion control endpoint (finish/stop, V2 decisions and retries)."""
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.app.adapters.base import CLIAdapter
from backend.app.api.mission_detail import build_mission_detail
from backend.app.api import missions
from backend.app.extensions.discussion.requests import DiscussionControlRequest
from backend.app.extensions.discussion.roundtable import Roundtable
from backend.app.settings import Settings, snapshot_settings
from backend.app.storage.repository import Repository


def build_router(settings: Settings, repository: Repository, adapter_override: Optional[CLIAdapter] = None) -> APIRouter:
    router = APIRouter(prefix="/api/missions", tags=["missions"])
    repo = repository

    @router.post("/{mission_id}/discussion-controls", status_code=202)
    def discussion_control(mission_id: str, payload: DiscussionControlRequest):
        from backend.app.extensions.discussion.deliberation import Deliberation, is_v2
        if is_v2(repo.get_mission(mission_id)):
            mission_settings = snapshot_settings(settings)
            target, schedule = Deliberation(repo).control(mission_id, payload, mission_settings)
            detail = build_mission_detail(repo, settings, target)
            if schedule:
                threading.Thread(target=missions._run_mission_in_background,
                                 args=(repo, mission_settings, target, adapter_override), daemon=True).start()
            return detail
        if payload.action not in {'finish', 'stop'}:
            raise HTTPException(status_code=422, detail='Control requires a V2 discussion')
        Roundtable(repo).control(mission_id, payload.action, payload.request_id, payload.summary_agent_id)
        return build_mission_detail(repo, settings, mission_id)

    return router
