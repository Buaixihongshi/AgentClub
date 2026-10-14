from __future__ import annotations

from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.domain.maintenance import MaintenanceFeedback, RegisteredRepository
from backend.app.maintenance.service import MaintenanceService
from backend.app.maintenance.workspace import WorkspaceError
from backend.app.settings import Settings
from backend.app.storage.repository import Repository


class RegisterRepositoryRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    local_path: Path
    github_repo: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    remote_name: str = "origin"
    default_branch: str = "main"
    validation_commands: List[List[str]] = Field(min_length=1)
    forbidden_paths: List[str] = Field(default_factory=lambda: [".env", "secrets/"])
    max_changed_files: int = Field(default=30, ge=1, le=1000)
    max_repair_rounds: int = Field(default=3, ge=0, le=20)


class CreateMaintenanceRunRequest(BaseModel):
    repository_id: str
    goal: str = Field(min_length=1)
    issue_url: Optional[str] = None


class ApprovalRequest(BaseModel):
    decision: str = Field(pattern=r"^(approved|rejected)$")
    note: Optional[str] = None


class FeedbackRequest(BaseModel):
    rating: str = Field(pattern=r"^(satisfied|mostly_satisfied|dissatisfied)$")
    reason_tags: List[str] = Field(default_factory=list)
    comment: Optional[str] = None
    objective_success: bool
    agent_evaluation: Optional[str] = None


class EvolutionRequest(BaseModel):
    trigger: str = Field(min_length=1)
    proposal: str = Field(min_length=1)
    repository_id: Optional[str] = None


def build_router(settings: Settings, repository: Repository) -> APIRouter:
    router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])
    service = MaintenanceService(repository, settings)

    @router.get("/repositories")
    def list_repositories():
        return [item.model_dump(mode="json") for item in repository.list_registered_repositories()]

    @router.post("/repositories", status_code=201)
    def register_repository(payload: RegisterRepositoryRequest):
        candidate = RegisteredRepository(
            id="rr_{suffix}".format(suffix=uuid4().hex),
            **payload.model_dump(),
        )
        try:
            registered = service.register_repository(candidate)
        except WorkspaceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return registered.model_dump(mode="json")

    @router.get("/runs")
    def list_runs(repository_id: Optional[str] = None):
        return [
            item.model_dump(mode="json")
            for item in repository.list_maintenance_runs(repository_id)
        ]

    @router.post("/runs", status_code=201)
    def create_run(payload: CreateMaintenanceRunRequest):
        try:
            run = service.create_run(
                repository_id=payload.repository_id,
                goal=payload.goal,
                issue_url=payload.issue_url,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return service.detail(run.id)

    @router.get("/runs/{run_id}")
    def get_run(run_id: str):
        try:
            return service.detail(run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/validate")
    def validate_run(run_id: str):
        try:
            service.validate_run(run_id)
            return service.detail(run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/resume")
    def resume_run(run_id: str):
        try:
            service.resume_run(run_id)
            return service.detail(run_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/runs/{run_id}/approvals", status_code=201)
    def approve_run(run_id: str, payload: ApprovalRequest):
        try:
            approval = service.record_approval(run_id, payload.decision, payload.note)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return approval.model_dump(mode="json")

    @router.post("/runs/{run_id}/feedback", status_code=201)
    def add_feedback(run_id: str, payload: FeedbackRequest):
        feedback = MaintenanceFeedback(
            id="mf_{suffix}".format(suffix=uuid4().hex),
            run_id=run_id,
            **payload.model_dump(),
        )
        try:
            candidate = service.record_feedback(feedback)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "feedback": feedback.model_dump(mode="json"),
            "memory_candidate": candidate.model_dump(mode="json"),
        }

    @router.get("/memory-candidates")
    def list_memory_candidates(repository_id: Optional[str] = None):
        return [
            item.model_dump(mode="json")
            for item in repository.list_memory_candidates(repository_id)
        ]

    @router.post("/evolution-batches", status_code=201)
    def create_evolution_batch(payload: EvolutionRequest):
        try:
            batch = service.create_evolution_batch(
                trigger=payload.trigger,
                proposal=payload.proposal,
                repository_id=payload.repository_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return batch.model_dump(mode="json")

    @router.get("/evolution-batches")
    def list_evolution_batches():
        return [item.model_dump(mode="json") for item in repository.list_evolution_batches()]

    return router
