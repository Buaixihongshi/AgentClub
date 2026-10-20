"""Task definitions and rubric schema for the multi-agent evaluation suite."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


CheckKind = Literal[
    "contains_all",
    "contains_any",
    "not_contains",
    "regex",
    "not_regex",
    "min_matches",
    "max_chars",
    "min_chars",
    "json_fields",
    "json_exact",
    "equals",
]


class RubricCheck(BaseModel):
    id: str = Field(min_length=1)
    kind: CheckKind
    weight: float = Field(default=1.0, gt=0)
    description: str = ""
    values: List[str] = Field(default_factory=list)
    value: Optional[Union[str, int, float, Dict[str, object]]] = None
    count: Optional[int] = Field(default=None, ge=1)
    case_sensitive: bool = False

    @model_validator(mode="after")
    def _validate_shape(self):
        if self.kind in {"contains_all", "contains_any", "not_contains"}:
            if not self.values:
                raise ValueError("{kind} needs a non-empty values list".format(kind=self.kind))
        if self.kind in {"regex", "not_regex", "min_matches", "equals"}:
            if not isinstance(self.value, str) or not self.value:
                raise ValueError("{kind} needs a non-empty string value".format(kind=self.kind))
        if self.kind == "min_matches" and self.count is None:
            raise ValueError("min_matches needs count")
        if self.kind in {"max_chars", "min_chars"}:
            if not isinstance(self.value, int) or isinstance(self.value, bool) or self.value < 0:
                raise ValueError("{kind} needs a non-negative integer value".format(kind=self.kind))
        if self.kind in {"json_fields", "json_exact"}:
            if not isinstance(self.value, dict) or not self.value:
                raise ValueError("{kind} needs a non-empty object value".format(kind=self.kind))
        return self


class Rubric(BaseModel):
    threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    checks: List[RubricCheck] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_ids(self):
        ids = [check.id for check in self.checks]
        if len(set(ids)) != len(ids):
            raise ValueError("rubric check ids must be unique")
        return self

    @property
    def total_weight(self) -> float:
        return sum(check.weight for check in self.checks)


class EvalTask(BaseModel):
    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    rubric: Rubric
    notes: str = ""
    tags: List[str] = Field(default_factory=list)


class EvalTaskSet(BaseModel):
    schema_version: int = 1
    tasks: List[EvalTask] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_ids(self):
        ids = [task.id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("task ids must be unique")
        return self

    def categories(self) -> List[str]:
        seen: List[str] = []
        for task in self.tasks:
            if task.category not in seen:
                seen.append(task.category)
        return seen

    def by_id(self, task_id: str) -> EvalTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)


def load_tasks(path: Path) -> EvalTaskSet:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return EvalTaskSet.model_validate(raw)


def select_tasks(
    task_set: EvalTaskSet,
    *,
    categories: Optional[List[str]] = None,
    task_ids: Optional[List[str]] = None,
) -> List[EvalTask]:
    tasks = list(task_set.tasks)
    if categories:
        wanted = set(categories)
        tasks = [task for task in tasks if task.category in wanted]
    if task_ids:
        wanted_ids = set(task_ids)
        tasks = [task for task in tasks if task.id in wanted_ids]
    if not tasks:
        raise ValueError("No tasks matched the requested filters")
    return tasks
