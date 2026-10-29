"""GET /api/observability — the turn-level view that replaces the side-rail trace.

One endpoint, one payload: turns with their steps, the read-time checks, how
much of the trace is actually visible, and what the turns cost. Scoping is
either one mission (`?mission_id=`) or the most recent missions.
"""
from fastapi import APIRouter, HTTPException, Query

from backend.app.observability.overview import WINDOWS, collect_overview
from backend.app.observability.service import agent_names, build_payload, turn_detail
from backend.app.storage.repository import Repository


def build_router(repository: Repository) -> APIRouter:
    router = APIRouter(prefix="/api/observability", tags=["observability"])

    @router.get("")
    def get_observability(
        mission_id: str | None = Query(default=None, description="scope to one mission"),
        limit: int = Query(default=50, ge=1, le=200),
        mission_limit: int = Query(default=20, ge=1, le=100),
    ):
        if mission_id and repository.get_mission(mission_id) is None:
            raise HTTPException(status_code=404, detail=f"mission not found: {mission_id}")
        return build_payload(
            repository,
            mission_id=mission_id,
            limit=limit,
            mission_limit=mission_limit,
        )

    @router.get("/overview")
    def get_overview(
        window: str = Query(default="all"),
        limit: int = Query(default=10, ge=0, le=100),
    ):
        """Every conversation in one payload (settings → 观测)."""
        if window not in WINDOWS:
            raise HTTPException(status_code=422, detail=f"unknown window: {window}")
        return collect_overview(
            repository,
            window=window,
            recent=limit,
            agent_names=agent_names(),
        )

    @router.get("/turns/{turn_id}")
    def get_turn(turn_id: str):
        detail = turn_detail(repository, turn_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"turn not found: {turn_id}")
        return detail

    @router.get("/turns/{turn_id}/records")
    def get_turn_records(turn_id: str):
        """The complete private execution records (prompt, raw stream, environment)."""
        from backend.app.adapters.turn_record import read_records
        from backend.app.orchestration.deliberation import Deliberation, is_v2

        mission_id = repository.mission_id_for_turn(turn_id)
        mission = repository.get_mission(mission_id) if mission_id else None
        if mission is None:
            raise HTTPException(status_code=404, detail=f"turn not found: {turn_id}")
        # A V2 discussion releases results in batches; its raw streams contain
        # unreleased submissions, so they open only once the discussion ends.
        if is_v2(mission) and Deliberation(repository).state(mission)["phase"] != "ended":
            raise HTTPException(status_code=409, detail="讨论结束后才能查看执行记录")
        return {"turn_id": turn_id, "mission_id": mission.id,
                "records": read_records(repository.database_path, mission.id, turn_id)}

    return router
