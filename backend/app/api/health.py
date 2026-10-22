from fastapi import APIRouter, HTTPException

from backend.app.config.providers import load_cli_providers
from backend.app.settings import Settings
from backend.app.storage.repository import Repository
from backend.app.orchestration.deliberation import BUILD_ID, RULES_VERSION, digest
from backend.app.orchestration.discussion_rules import COMMON_DISCUSSION_RULES
from backend.app.domain.project_skills import PROJECT_SKILLS
from backend.app.runtime_identity import capture_runtime, runtime_view


def build_router(settings: Settings, repository: Repository) -> APIRouter:
    router = APIRouter(prefix="/api/health", tags=["health"])
    started = capture_runtime(settings)

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
            "discussion_protocols": ["roundtable_v1", "deliberation_v2"],
            "project_skills": list(PROJECT_SKILLS),
            "discussion_rules_version": RULES_VERSION,
            "discussion_rules_hash": digest(COMMON_DISCUSSION_RULES),
            "build_id": BUILD_ID,
            "runtime": runtime_view(started),
        }

    return router
