from typing import Optional

from fastapi import FastAPI

from backend.app.api.agents import build_router as build_agents_router
from backend.app.api.conversations import build_router as build_conversations_router
from backend.app.api.hub_settings import build_router as build_hub_settings_router
from backend.app.api.health import build_router as build_health_router
from backend.app.api.missions import build_router as build_missions_router
from backend.app.api.onboarding import build_router as build_onboarding_router
from backend.app.api.providers import build_router as build_providers_router
from backend.app.adapters.base import CLIAdapter
from backend.app.settings import Settings, get_settings
from backend.app.storage.repository import Repository


def create_app(
    settings: Optional[Settings] = None,
    *,
    onboarding_verification_adapter: Optional[CLIAdapter] = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    repository = Repository(app_settings.database_path)
    repository.initialize()
    app_settings.artifact_root.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="AgentClub")
    app.include_router(build_health_router(app_settings, repository))
    app.include_router(build_agents_router(app_settings))
    app.include_router(build_conversations_router(app_settings, repository))
    app.include_router(build_hub_settings_router(app_settings))
    app.include_router(build_missions_router(app_settings, repository))
    app.include_router(
        build_onboarding_router(
            app_settings,
            repository,
            verification_adapter=onboarding_verification_adapter,
        )
    )
    app.include_router(build_providers_router(app_settings))
    return app


class LazyASGIApp:
    def __init__(self):
        self._app = None

    async def __call__(self, scope, receive, send):
        if self._app is None:
            self._app = create_app()
        await self._app(scope, receive, send)


app = LazyASGIApp()
