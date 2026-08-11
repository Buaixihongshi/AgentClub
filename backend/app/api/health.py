from fastapi import APIRouter, HTTPException

from backend.app.config.providers import load_cli_providers
from backend.app.settings import Settings
from backend.app.storage.repository import Repository


def build_router(settings: Settings, repository: Repository) -> APIRouter:
    router = APIRouter(prefix="/api/health", tags=["health"])

    @router.get("")
    def get_health():
        try:
            # Exercise the database rather than only reporting that the file exists.
            repository.list_conversations()
            providers = load_cli_providers(settings.config_dir)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="AgentClub is not ready: {error}".format(error=exc),
            ) from exc

        if not any(provider.id == "fake" for provider in providers):
            raise HTTPException(
                status_code=503,
                detail="AgentClub is not ready: fake provider is missing",
            )

        return {
            "status": "ok",
            "version": "0.1.0",
            "database": "ready",
            "config": "ready",
            "fake_provider": "ready",
        }

    return router
