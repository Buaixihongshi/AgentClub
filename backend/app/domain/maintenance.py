from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.domain.models import utc_now


class MaintenancePhase(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    LOCAL_VERIFYING = "local_verifying"
    REVIEWING = "reviewing"
    DRAFT_PR = "draft_pr"
    CI_WAITING = "ci_waiting"
    CI_REPAIRING = "ci_repairing"
    READY_FOR_APPROVAL = "ready_for_approval"
    MERGING = "merging"
    COMPLETED = "completed"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_MAINTENANCE_PHASES = {
    MaintenancePhase.COMPLETED,
    MaintenancePhase.BLOCKED,
    MaintenancePhase.FAILED,
    MaintenancePhase.CANCELLED,
}


class RegisteredRepository(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=120)
    local_path: Path
    github_repo: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    remote_name: str = Field(default="origin", min_length=1, max_length=80)
    default_branch: str = Field(default="main", min_length=1, max_length=180)
    validation_commands: List[List[str]] = Field(min_length=1)
    forbidden_paths: List[str] = Field(default_factory=lambda: [".env", "secrets/"])
    max_changed_files: int = Field(default=30, ge=1, le=1000)
    max_repair_rounds: int = Field(default=3, ge=0, le=20)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("validation_commands")
    @classmethod
    def validate_commands(cls, value: List[List[str]]) -> List[List[str]]:
        for command in value:
            if not command or any(not isinstance(part, str) or not part.strip() for part in command):
                raise ValueError("each validation command must be a non-empty argument array")
        return value

    @field_validator("forbidden_paths")
    @classmethod
    def validate_forbidden_paths(cls, value: List[str]) -> List[str]:
        for path in value:
            candidate = Path(path)
            if not path.strip() or candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("forbidden paths must be safe repository-relative paths")
        return value


class MaintenanceRun(BaseModel):
    id: str
    repository_id: str
    goal: str = Field(min_length=1)
    issue_url: Optional[str] = None
    phase: MaintenancePhase = MaintenancePhase.QUEUED
    branch_name: Optional[str] = None
    workspace_path: Optional[Path] = None
    repair_round: int = 0
    mission_ids: List[str] = Field(default_factory=list)
    pull_request_url: Optional[str] = None
    pull_request_number: Optional[int] = None
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_source(self) -> "MaintenanceRun":
        if not self.goal.strip():
            raise ValueError("goal must not be empty")
        return self


class ValidationResult(BaseModel):
    id: str
    run_id: str
    command: List[str]
    exit_code: int
    duration_ms: int = Field(ge=0)
    stdout: str = ""
    stderr: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class MaintenanceApproval(BaseModel):
    id: str
    run_id: str
    decision: str = Field(pattern=r"^(approved|rejected)$")
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class MaintenanceFeedback(BaseModel):
    id: str
    run_id: str
    rating: str = Field(pattern=r"^(satisfied|mostly_satisfied|dissatisfied)$")
    reason_tags: List[str] = Field(default_factory=list)
    comment: Optional[str] = None
    objective_success: bool
    agent_evaluation: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class MemoryCandidate(BaseModel):
    id: str
    run_id: str
    repository_id: str
    content: str
    status: str = Field(default="candidate", pattern=r"^(candidate|promoted|rejected)$")
    evidence_count: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class EvolutionBatch(BaseModel):
    id: str
    repository_id: Optional[str] = None
    trigger: str
    status: str = Field(default="proposed", pattern=r"^(proposed|evaluating|pr_open|completed|rejected)$")
    proposal: str
    eval_cases: List[str] = Field(default_factory=list)
    pull_request_url: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
