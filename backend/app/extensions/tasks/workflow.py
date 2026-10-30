"""Human-owned workflow plans materialized as ordinary, evidence-backed work.

A plan is not a second executor. Its nodes use the existing scoped work,
provider grants, budgets, candidate checks and independent-review policy.
"""
from typing import Literal

from pydantic import Field, model_validator

from backend.app.domain.agent_registry import get_agent_registry
from backend.app.extensions.tasks.adaptive_models import Agreement
from backend.app.extensions.tasks.models import Input
from backend.app.extensions.tasks.service import WorkError, now


class WorkflowStep(Input):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,60}$')
    title: str = Field(min_length=1, max_length=200)
    owner_id: str
    goal: str = Field(min_length=1, max_length=4000)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    criterion_ids: list[str] | None = Field(default=None, max_length=40)


class Workflow(Input):
    mode: Literal['adaptive', 'workflow'] = 'adaptive'
    steps: list[WorkflowStep] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def graph(self):
        if self.mode == 'adaptive' and self.steps:
            raise ValueError('自主编排不接受固定步骤')
        if self.mode == 'workflow' and not self.steps:
            raise ValueError('工作流至少需要一个步骤')
        ids = {s.id for s in self.steps}
        if len(ids) != len(self.steps):
            raise ValueError('步骤 ID 不得重复')
        visiting, visited = set(), set()
        by_id = {s.id: s for s in self.steps}

        def visit(key):
            if key in visiting:
                raise ValueError('步骤依赖不能形成循环')
            if key in visited:
                return
            visiting.add(key)
            step = by_id[key]
            if len(set(step.depends_on)) != len(step.depends_on):
                raise ValueError('步骤依赖不能重复')
            for dep in step.depends_on:
                if dep not in ids:
                    raise ValueError('前置步骤不存在')
                visit(dep)
            visiting.remove(key)
            visited.add(key)
        for key in by_id:
            visit(key)
        return self


def configure(service, board, root, value):
    """Called inside the host command transaction, never from model output."""
    plan = Workflow.model_validate(value)
    service._require_root_quiet(board, root)
    family = [t for t in board['tasks'] if t['root_id'] == root['id']]
    if not root['paused'] or root['status'] in {'done', 'cancelled'} or any(t['attempts'] for t in family):
        raise WorkError('仅暂停且尚未执行的任务可以修改编排')
    if any(t['parent_id'] and not t.get('workflow_step_id') for t in family):
        raise WorkError('已有协作分工，不能替换为另一份编排')
    agents = set(get_agent_registry().ids())
    agreement = service._agreement(board, root)
    criteria = {c['id']: c for c in agreement['criteria']}
    for step in plan.steps:
        if step.owner_id not in agents:
            raise WorkError('步骤负责人不存在', 422)
        if step.criterion_ids is not None and (not step.criterion_ids
                or len(set(step.criterion_ids)) != len(step.criterion_ids)
                or not set(step.criterion_ids) <= set(criteria)):
            raise WorkError('步骤验收项必须引用当前约定中的有效编号', 422)
    old = root.get('orchestration')
    if old:
        root.setdefault('workflow_history', []).append({'orchestration': old, 'replaced_at': now()})
    board['tasks'] = [t for t in board['tasks'] if t['id'] == root['id'] or t['root_id'] != root['id']]
    root['depends_on'] = []
    root['orchestration'] = plan.model_dump(exclude_none=True)
    nodes = {}
    for step, definition in zip(plan.steps, root['orchestration']['steps']):
        # Step goals can narrow the action, never broaden root scope or authority.
        child_agreement = service._add_agreement(board, {
            **{k: agreement[k] for k in Agreement.model_fields}, 'goal': step.goal,
            'criteria': [criteria[k] for k in step.criterion_ids] if step.criterion_ids else
                        [{'id': 'OUT-1', 'text': step.goal[:2000]}],
            'validation_commands': [],
        }, 'human')
        child = service._new_task(step.title, step.owner_id, child_agreement,
                                  [c['id'] for c in child_agreement['criteria']], parent=root)
        child['workflow_step_id'] = step.id
        child['next_action'] = step.goal
        nodes[step.id] = child
        definition['task_id'] = child['id']  # Host-issued identity; rejected in input.
        board['tasks'].append(child)
    for step in plan.steps:
        nodes[step.id]['depends_on'] = [nodes[key]['id'] for key in step.depends_on]
    root['depends_on'] = [nodes[s.id]['id'] for s in plan.steps]
    if nodes:
        service._waiting(root, 'dependency', '等待工作流步骤完成，再由负责人整体验收', '协作者')
    root['next_action'] = '整合工作流结果并按原始约定完成全部验收' if nodes else agreement['goal']
