"""Host loop for one adaptive work action (moved out of the core runner)."""
from uuid import uuid4

from backend.app.domain.models import MissionStatus, TurnStatus, utc_now
from backend.app.extensions.tasks.rules import adaptive_prompt_rules
from backend.app.mcp.tools import MCPTools
from backend.app.memory import handover as handover_store
from backend.app.memory.assembly import assemble, sections_into_prompt
from backend.app.orchestration.runner import _providers_by_agent


def run_adaptive(runner, mission):
    """One necessary action per mission; durable work controls continuation."""
    from backend.app.extensions.tasks.service import SharedWork
    from backend.app.extensions.tasks.snapshots import workspace_state
    import json
    work = SharedWork(runner.repository)
    context = work.execution_context(mission.id)
    if not context or context['task']['status'] == 'cancelled':
        return mission
    context['workspace_state'] = workspace_state(context['workspace_root'])
    agent_id = mission.target_agent_id
    agent = next(a for a in runner.agents if a.id == agent_id)
    mission = mission.model_copy(update={'status': MissionStatus.RUNNING, 'updated_at': utc_now()})
    runner.repository.update_mission(mission)
    turn_id = 'turn_work_' + uuid4().hex[:12]
    tools = MCPTools(runner.repository, runner.settings.artifact_root, valid_agent_ids={a.id for a in runner.agents},
                     workspace_root=context['workspace_root'], current_step_origin='adaptive')
    prompt = (
        f'你是{agent.display_name}。人格：{agent.persona}\n'
        + adaptive_prompt_rules() + '\n'
        '可协作角色：\n' + json.dumps([{'id': a.id, 'name': a.display_name, 'role': a.role} for a in runner.agents], ensure_ascii=False)
        + '\n持久工作上下文：\n' + json.dumps(context, ensure_ascii=False)
    )
    sections = assemble(runner.repository, runner.settings.memory_root,
                        mission.conversation_id, agent_id)
    memory_blocks_text = '\n'.join(sections_into_prompt(sections))
    if memory_blocks_text:
        prompt = prompt + '\n' + memory_blocks_text + '\n'
    outcome = runner._run_turn_with_fallbacks(mission, agent_id, turn_id,
                                           _providers_by_agent([agent])[agent_id], prompt, tools)
    handover_store.acknowledge(runner.repository, agent_id)
    status = MissionStatus.COMPLETED if outcome.status == TurnStatus.COMPLETED else MissionStatus.BLOCKED
    mission = runner.repository.get_mission(mission.id).model_copy(update={'status': status, 'updated_at': utc_now()})
    runner.repository.update_mission(mission)
    runner.repository.add_event(mission.id, 'status', {'status': status.value, 'summary': outcome.summary or status.value})
    return mission

