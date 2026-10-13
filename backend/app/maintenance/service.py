from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from backend.app.domain.maintenance import (
    TERMINAL_MAINTENANCE_PHASES,
    EvolutionBatch,
    MaintenanceApproval,
    MaintenanceFeedback,
    MaintenancePhase,
    MaintenanceRun,
    MemoryCandidate,
    RegisteredRepository,
)
from backend.app.domain.models import utc_now
from backend.app.maintenance.validation import ValidationEngine, ValidationPolicyError
from backend.app.maintenance.workspace import WorkspaceError, WorkspaceManager, validate_repository_path
from backend.app.settings import Settings
from backend.app.storage.repository import Repository


class MaintenanceService:
    def __init__(self, repository: Repository, settings: Settings):
        self.repository = repository
        self.settings = settings
        self.workspaces = WorkspaceManager(settings.maintenance_worktree_root)
        self.validation = ValidationEngine(settings.maintenance_command_timeout)

    def register_repository(self, candidate: RegisteredRepository) -> RegisteredRepository:
        path = validate_repository_path(candidate.local_path)
        normalized = candidate.model_copy(
            update={"local_path": path, "updated_at": utc_now()}
        )
        self.repository.save_registered_repository(normalized)
        return normalized

    def create_run(
        self,
        repository_id: str,
        goal: str,
        issue_url: str | None = None,
    ) -> MaintenanceRun:
        if not self.settings.maintenance_enabled:
            raise RuntimeError("Repository maintenance is disabled")
        registered = self.repository.get_registered_repository(repository_id)
        if registered is None:
            raise LookupError("Registered repository not found")
        if not registered.enabled:
            raise RuntimeError("Registered repository is disabled")
        run = MaintenanceRun(
            id="mr_{suffix}".format(suffix=uuid4().hex),
            repository_id=repository_id,
            goal=goal,
            issue_url=issue_url,
        )
        self.repository.create_maintenance_run(run)
        self.repository.add_maintenance_event(run.id, "workspace.preparing", {})
        try:
            prepared = self.workspaces.prepare(registered, run).model_copy(
                update={"phase": MaintenancePhase.PLANNING, "updated_at": utc_now()}
            )
        except WorkspaceError as exc:
            prepared = run.model_copy(
                update={
                    "phase": MaintenancePhase.BLOCKED,
                    "last_error": str(exc),
                    "updated_at": utc_now(),
                }
            )
        self.repository.update_maintenance_run(prepared)
        self.repository.add_maintenance_event(
            prepared.id,
            "run.phase_changed",
            {"phase": prepared.phase.value, "error": prepared.last_error},
        )
        return prepared

    def recover_interrupted_runs(self) -> list[MaintenanceRun]:
        transient = {
            MaintenancePhase.IMPLEMENTING,
            MaintenancePhase.LOCAL_VERIFYING,
            MaintenancePhase.REVIEWING,
            MaintenancePhase.CI_REPAIRING,
            MaintenancePhase.MERGING,
        }
        recovered: list[MaintenanceRun] = []
        for run in self.repository.list_maintenance_runs():
            if run.phase not in transient:
                continue
            updated = run.model_copy(
                update={
                    "phase": MaintenancePhase.BLOCKED,
                    "last_error": "AgentClub restarted during an in-flight maintenance action",
                    "updated_at": utc_now(),
                }
            )
            self.repository.update_maintenance_run(updated)
            self.repository.add_maintenance_event(
                run.id,
                "run.interrupted",
                {"previous_phase": run.phase.value, "phase": updated.phase.value},
            )
            recovered.append(updated)
        return recovered

    def resume_run(self, run_id: str) -> MaintenanceRun:
        run = self._require_run(run_id)
        if run.phase not in {MaintenancePhase.BLOCKED, MaintenancePhase.FAILED}:
            raise RuntimeError("Only blocked or failed maintenance runs can be resumed")
        if run.workspace_path is None or not Path(run.workspace_path).is_dir():
            raise RuntimeError("Maintenance workspace is missing; create a new run")
        resumed = run.model_copy(
            update={
                "phase": MaintenancePhase.PLANNING,
                "last_error": None,
                "updated_at": utc_now(),
            }
        )
        self.repository.update_maintenance_run(resumed)
        self.repository.add_maintenance_event(
            run.id,
            "run.resumed",
            {"phase": resumed.phase.value},
        )
        return resumed

    def validate_run(self, run_id: str) -> MaintenanceRun:
        run = self._require_run(run_id)
        registered = self._require_repository(run.repository_id)
        if run.workspace_path is None:
            raise RuntimeError("Run has no prepared workspace")
        verifying = run.model_copy(
            update={"phase": MaintenancePhase.LOCAL_VERIFYING, "updated_at": utc_now()}
        )
        self.repository.update_maintenance_run(verifying)
        self.repository.add_maintenance_event(run.id, "validation.started", {})
        try:
            results = self.validation.run(run.id, registered, Path(run.workspace_path))
        except ValidationPolicyError as exc:
            final = verifying.model_copy(
                update={
                    "phase": MaintenancePhase.BLOCKED,
                    "last_error": str(exc),
                    "updated_at": utc_now(),
                }
            )
        else:
            for result in results:
                self.repository.add_validation_result(result)
            failed = next((result for result in results if result.exit_code != 0), None)
            final = verifying.model_copy(
                update={
                    "phase": MaintenancePhase.BLOCKED if failed else MaintenancePhase.REVIEWING,
                    "last_error": (
                        "Validation failed: {command}".format(command=" ".join(failed.command))
                        if failed else None
                    ),
                    "updated_at": utc_now(),
                }
            )
        self.repository.update_maintenance_run(final)
        self.repository.add_maintenance_event(
            run.id,
            "validation.completed",
            {"phase": final.phase.value, "error": final.last_error},
        )
        return final

    def record_approval(self, run_id: str, decision: str, note: str | None) -> MaintenanceApproval:
        run = self._require_run(run_id)
        if run.phase not in {MaintenancePhase.READY_FOR_APPROVAL, MaintenancePhase.WAITING_APPROVAL}:
            raise RuntimeError("Run is not waiting for approval")
        approval = MaintenanceApproval(
            id="ma_{suffix}".format(suffix=uuid4().hex),
            run_id=run_id,
            decision=decision,
            note=note,
        )
        self.repository.add_maintenance_approval(approval)
        if decision == "rejected":
            updated = run.model_copy(
                update={
                    "phase": MaintenancePhase.BLOCKED,
                    "last_error": "Merge rejected by user",
                    "updated_at": utc_now(),
                }
            )
            self.repository.update_maintenance_run(updated)
        self.repository.add_maintenance_event(
            run_id, "approval.recorded", approval.model_dump(mode="json")
        )
        return approval

    def record_feedback(self, feedback: MaintenanceFeedback) -> MemoryCandidate:
        run = self._require_run(feedback.run_id)
        if run.phase not in TERMINAL_MAINTENANCE_PHASES:
            raise RuntimeError("Feedback can only be recorded for a finished maintenance run")
        self.repository.add_maintenance_feedback(feedback)
        positive = feedback.objective_success and feedback.rating != "dissatisfied"
        candidate = MemoryCandidate(
            id="mc_{suffix}".format(suffix=uuid4().hex),
            run_id=run.id,
            repository_id=run.repository_id,
            content=self._feedback_lesson(run, feedback),
            status="candidate" if positive else "rejected",
        )
        self.repository.add_memory_candidate(candidate)
        self.repository.add_maintenance_event(
            run.id,
            "feedback.recorded",
            {"feedback_id": feedback.id, "memory_candidate_id": candidate.id},
        )
        return candidate

    def create_evolution_batch(
        self, trigger: str, proposal: str, repository_id: str | None = None
    ) -> EvolutionBatch:
        if repository_id is not None:
            self._require_repository(repository_id)
        batch = EvolutionBatch(
            id="eb_{suffix}".format(suffix=uuid4().hex),
            repository_id=repository_id,
            trigger=trigger,
            proposal=proposal,
            eval_cases=[
                "Re-run relevant successful maintenance traces",
                "Re-run failure cases associated with this proposal",
            ],
        )
        self.repository.add_evolution_batch(batch)
        return batch

    def detail(self, run_id: str) -> dict:
        run = self._require_run(run_id)
        return {
            "run": run.model_dump(mode="json"),
            "repository": self._require_repository(run.repository_id).model_dump(mode="json"),
            "validation_results": [
                item.model_dump(mode="json")
                for item in self.repository.list_validation_results(run_id)
            ],
            "approvals": [
                item.model_dump(mode="json")
                for item in self.repository.list_maintenance_approvals(run_id)
            ],
            "feedback": [
                item.model_dump(mode="json")
                for item in self.repository.list_maintenance_feedback(run_id)
            ],
            "events": self.repository.list_maintenance_events(run_id),
        }

    def _require_run(self, run_id: str) -> MaintenanceRun:
        run = self.repository.get_maintenance_run(run_id)
        if run is None:
            raise LookupError("Maintenance run not found")
        return run

    def _require_repository(self, repository_id: str) -> RegisteredRepository:
        registered = self.repository.get_registered_repository(repository_id)
        if registered is None:
            raise LookupError("Registered repository not found")
        return registered

    @staticmethod
    def _feedback_lesson(run: MaintenanceRun, feedback: MaintenanceFeedback) -> str:
        parts = ["Task: {goal}".format(goal=run.goal), "Rating: {rating}".format(rating=feedback.rating)]
        if feedback.reason_tags:
            parts.append("Reasons: {tags}".format(tags=", ".join(feedback.reason_tags)))
        if feedback.comment:
            parts.append("Feedback: {comment}".format(comment=feedback.comment))
        return "\n".join(parts)
