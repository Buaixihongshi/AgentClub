from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Criterion(Input):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,60}$')
    text: str = Field(min_length=1, max_length=2000)


class ContractInput(Input):
    source: str = Field(min_length=1, max_length=12000)
    goal: str = Field(min_length=1, max_length=4000)
    scope: str = Field(min_length=1, max_length=4000)
    non_goals: str = Field(min_length=1, max_length=4000)
    criteria: list[Criterion] = Field(min_length=1, max_length=40)
    validation_commands: list[list[str]] = Field(min_length=1, max_length=12)

    @field_validator('validation_commands')
    @classmethod
    def commands(cls, value):
        if any(not cmd or len(cmd) > 100 or any(not arg or len(arg) > 2000 for arg in cmd) for cmd in value):
            raise ValueError('验证命令必须是非空参数数组，不经过 shell')
        return value

    @model_validator(mode='after')
    def unique_criteria(self):
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError('验收项编号不能重复')
        return self


class TaskInput(Input):
    title: str = Field(min_length=1, max_length=200)
    contract_id: str
    owner_id: str
    reviewer_id: str
    criterion_ids: list[str] = Field(min_length=1, max_length=40)
    depends_on: list[str] = Field(default_factory=list, max_length=100)


class WorkCommand(Input):
    action: Literal['propose_work', 'accept_proposal', 'reject_proposal', 'propose_contract', 'activate_contract', 'create_task', 'start', 'pause',
                    'retry', 'progress', 'submit', 'review', 'block', 'create_work', 'revise_work',
                    'decide', 'resume', 'cancel', 'adjust_budget', 'grant_execution', 'continue_work',
                    'request_decision', 'raise_risk', 'propose_revision', 'propose_transfer', 'accept_transfer', 'refine_agreement', 'retry_environment', 'start_preview', 'stop_preview']
    expected_revision: int = Field(ge=0)
    data: dict = Field(default_factory=dict)
    operation_id: str | None = Field(default=None, min_length=1, max_length=120)
