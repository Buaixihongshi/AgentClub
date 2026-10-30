"""Input contracts for durable, outcome-oriented work; actions are not stages."""
from typing import Literal

from pydantic import Field, model_validator

from backend.app.extensions.tasks.models import Input, Criterion, ContractInput


Risk = Literal['local', 'security', 'data', 'migration', 'contract', 'governance']
Delivery = Literal['document', 'development', 'online']


class Agreement(Input):
    source: str = Field(min_length=1, max_length=12000)
    goal: str = Field(min_length=1, max_length=4000)
    scope: str = Field(min_length=1, max_length=4000)
    non_goals: str = Field(default='', max_length=4000)
    criteria: list[Criterion] = Field(min_length=1, max_length=40)
    validation_commands: list[list[str]] = Field(default_factory=list, max_length=12)
    delivery: Delivery = 'development'
    risks: list[Risk] = Field(default_factory=lambda: ['local'], min_length=1)

    @model_validator(mode='after')
    def validate_agreement(self):
        ContractInput.unique_criteria(self)
        ContractInput.commands(self.validation_commands)
        return self


class NewWork(Input):
    title: str = Field(min_length=1, max_length=200)
    owner_id: str
    agreement: Agreement


class Checkpoint(Input):
    task_id: str
    text: str = Field(min_length=1, max_length=4000)
    next_action: str = Field(min_length=1, max_length=4000)
    # Only persisted references count as observable progress.
    evidence_ids: list[str] = Field(default_factory=list)


class Submission(Input):
    task_id: str
    summary: str = Field(min_length=1, max_length=12000)
    artifact_paths: list[str] = Field(min_length=1, max_length=40)
    observations: list[Criterion] = Field(min_length=1, max_length=40)
    limitations: str = Field(default='', max_length=4000)
    reviewer_id: str | None = None
    kind: Literal['code', 'design', 'document']


class DecisionRequest(Input):
    task_id: str
    question: str = Field(min_length=1, max_length=2000)
    options: list[Criterion] = Field(min_length=1, max_length=8)
    recommendation: str = Field(default='', max_length=4000)
    artifact_paths: list[str] = Field(default_factory=list, max_length=20)
    kind: Literal['direction', 'design', 'authorization'] = 'direction'

    @model_validator(mode='after')
    def unique_options(self):
        if len({x.id for x in self.options}) != len(self.options):
            raise ValueError('决定选项 ID 必须唯一')
        return self


class Delegation(Input):
    task_id: str
    target_agent_id: str
    purpose: str = Field(min_length=1, max_length=4000)
    criterion_ids: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list, max_length=40)
    capability: str = Field(default='', max_length=1000)


POLICY = {
    'version': 'adaptive-v1',
    'review_risks': ['security', 'data', 'migration', 'contract', 'governance'],
    'max_attempts': 20,
    'max_retries': 2,
    'max_no_progress': 3,
}

AGENT_ACTIONS = {'propose_work', 'progress', 'continue_work', 'submit', 'review', 'block',
                 'request_decision', 'raise_risk', 'propose_revision', 'propose_transfer', 'refine_agreement', 'accept_transfer'}
HUMAN_ACTIONS = {'accept_proposal', 'reject_proposal', 'create_work', 'revise_work', 'decide', 'pause', 'resume', 'cancel',
                 'retry', 'adjust_budget', 'accept_transfer', 'grant_execution',
                 'retry_environment', 'start_preview', 'stop_preview', 'configure_workflow'}
