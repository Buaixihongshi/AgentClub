from fastapi import APIRouter

from backend.app.domain.agent_registry import get_agent_registry
from backend.app.settings import Settings


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/agents", tags=["agents"])

    @router.get("")
    def list_agents():
        registry = get_agent_registry(settings.config_dir)
        return {
            "agents": [
                {
                    "id": agent.id,
                    "display_name": agent.display_name,
                    "role": agent.role,
                    "aliases": agent.aliases,
                    "capability_hints": agent.capability_hints,
                }
                for agent in registry.all()
            ]
        }

    return router
