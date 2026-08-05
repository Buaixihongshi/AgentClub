from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from backend.app.config.providers import (
    ProviderDisabledError,
    ProviderNotFoundError,
    ProviderProbeError,
    diagnose_providers,
    run_provider_probe,
)
from backend.app.settings import Settings, snapshot_settings


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/providers", tags=["providers"])

    @router.get("")
    def list_providers():
        current_settings = snapshot_settings(settings)
        return [
            provider
            for provider in diagnose_providers(
                current_settings.config_dir,
                overrides=current_settings.provider_overrides,
            )
            if provider["id"] not in {"fake", "gemini"}
        ]

    @router.post("/{provider_id}/probe")
    def probe_provider(provider_id: str, request: ProbeRequest):
        current_settings = snapshot_settings(settings)
        try:
            return run_provider_probe(
                current_settings.config_dir,
                provider_id,
                request.prompt,
                overrides=current_settings.provider_overrides,
            )
        except ProviderNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProviderDisabledError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProviderProbeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
