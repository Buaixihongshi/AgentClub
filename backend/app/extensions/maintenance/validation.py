from __future__ import annotations

import subprocess
import time
from pathlib import Path
from uuid import uuid4

from backend.app.domain.maintenance import RegisteredRepository, ValidationResult


class ValidationPolicyError(RuntimeError):
    pass


class ValidationEngine:
    def __init__(self, command_timeout: int = 900, output_limit: int = 8000):
        self.command_timeout = command_timeout
        self.output_limit = output_limit

    def run(
        self,
        run_id: str,
        repository: RegisteredRepository,
        workspace: Path,
    ) -> list[ValidationResult]:
        self._check_change_policy(repository, workspace)
        results: list[ValidationResult] = []
        for command in repository.validation_commands:
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    command,
                    cwd=workspace,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.command_timeout,
                    check=False,
                )
                exit_code = completed.returncode
                stdout = completed.stdout
                stderr = completed.stderr
            except subprocess.TimeoutExpired as exc:
                exit_code = 124
                stdout = exc.stdout or ""
                stderr = (exc.stderr or "") + "\ncommand timed out"
            except OSError as exc:
                exit_code = 127
                stdout = ""
                stderr = str(exc)
            results.append(
                ValidationResult(
                    id="vr_{suffix}".format(suffix=uuid4().hex),
                    run_id=run_id,
                    command=list(command),
                    exit_code=exit_code,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    stdout=self._bounded(stdout),
                    stderr=self._bounded(stderr),
                )
            )
            if exit_code != 0:
                break
        return results

    def _check_change_policy(self, repository: RegisteredRepository, workspace: Path) -> None:
        try:
            completed = subprocess.run(
                ["git", "status", "--porcelain", "-z"],
                cwd=workspace,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            stderr = getattr(exc, "stderr", "") or ""
            detail = stderr.strip() or str(exc)
            raise ValidationPolicyError(
                "Unable to inspect workspace changes: {detail}".format(detail=detail[:500])
            ) from exc

        changed = self._parse_changed_paths(completed.stdout)
        unique = sorted(set(changed))
        if len(unique) > repository.max_changed_files:
            raise ValidationPolicyError(
                "Changed file limit exceeded ({count}/{limit})".format(
                    count=len(unique), limit=repository.max_changed_files
                )
            )
        for path in unique:
            normalized = path[2:] if path.startswith("./") else path
            for forbidden in repository.forbidden_paths:
                prefix = forbidden.rstrip("/")
                if normalized == prefix or normalized.startswith(prefix + "/"):
                    raise ValidationPolicyError("Forbidden path changed: {path}".format(path=path))

    @staticmethod
    def _parse_changed_paths(output: str) -> list[str]:
        """Parse porcelain v1 -z output, including both sides of renames/copies."""
        records = [record for record in output.split("\0") if record]
        changed: list[str] = []
        index = 0
        while index < len(records):
            record = records[index]
            status = record[:2]
            changed.append(record[3:] if len(record) > 3 else record)
            if "R" in status or "C" in status:
                index += 1
                if index < len(records):
                    changed.append(records[index])
            index += 1
        return changed

    def _bounded(self, value: str) -> str:
        if len(value) <= self.output_limit:
            return value
        return value[: self.output_limit] + "\n… output truncated"
