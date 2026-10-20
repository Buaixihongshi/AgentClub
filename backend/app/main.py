from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from backend.app.orchestration.roundtable import Roundtable, DiscussionError

from backend.app.api.agents import build_router as build_agents_router
from backend.app.api.conversations import build_router as build_conversations_router
from backend.app.api.hub_settings import build_router as build_hub_settings_router
from backend.app.api.health import build_router as build_health_router
from backend.app.api.maintenance import build_router as build_maintenance_router
from backend.app.api.missions import build_router as build_missions_router
from backend.app.api.observability import build_router as build_observability_router
from backend.app.api.onboarding import build_router as build_onboarding_router
from backend.app.api.providers import build_router as build_providers_router
from backend.app.adapters.base import CLIAdapter
from backend.app.maintenance.service import MaintenanceService
from backend.app.settings import Settings, get_settings
from backend.app.storage.repository import Repository


def create_app(
    settings: Optional[Settings] = None,
    *,
    onboarding_verification_adapter: Optional[CLIAdapter] = None,
    mission_adapter: Optional[CLIAdapter] = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    repository = Repository(app_settings.database_path)
    repository.initialize()
    from backend.app.storage.execution import ExecutionLedger
    ExecutionLedger(repository).recover()
    Roundtable(repository).recover()
    MaintenanceService(repository, app_settings).recover_interrupted_runs()
    app_settings.artifact_root.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            if app_settings.antigravity_persistent:
                from backend.app.adapters.antigravity_session import SESSION_POOL
                SESSION_POOL.close(app_settings.database_path)

    app = FastAPI(title="AgentClub", lifespan=lifespan)

    @app.exception_handler(DiscussionError)
    async def discussion_error_handler(request, exc):
        return JSONResponse(status_code=exc.status_code, content={'detail': str(exc)})

    app.include_router(build_health_router(app_settings, repository))
    app.include_router(build_maintenance_router(app_settings, repository))
    app.include_router(build_agents_router(app_settings))
    app.include_router(build_conversations_router(app_settings, repository, adapter_override=mission_adapter))
    app.include_router(build_hub_settings_router(app_settings))
    app.include_router(build_missions_router(app_settings, repository, adapter_override=mission_adapter))
    app.include_router(
        build_onboarding_router(
            app_settings,
            repository,
            verification_adapter=onboarding_verification_adapter,
        )
    )
    app.include_router(build_providers_router(app_settings))
    app.include_router(build_observability_router(repository))
    # A persisted human choice may have committed just before a crash and
    # before its worker started. Reservations make rescheduling idempotent.
    from backend.app.orchestration.deliberation import is_v2
    from backend.app.api.missions import _run_mission_in_background
    from backend.app.domain.models import MissionStatus
    import threading
    for pending in repository.list_missions():
        if is_v2(pending) and pending.status == MissionStatus.PENDING:
            threading.Thread(target=_run_mission_in_background,
                             args=(repository, app_settings, pending.id, mission_adapter), daemon=True).start()
    return app


class LazyASGIApp:
    def __init__(self):
        self._app = None

    async def __call__(self, scope, receive, send):
        if self._app is None:
            self._app = create_app()
        await self._app(scope, receive, send)


app = LazyASGIApp()
