"""Host loop for one adaptive work action (moved out of the core runner)."""
from uuid import uuid4

from backend.app.domain.models import MissionStatus, TurnStatus, utc_now
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
        '你负责推进持久工作卡的一项必要动作。按实际缺口决定调查、设计、实现、验证或协作，简单工作可本轮完成。'
        '先 shared_work(read) 读取最新 revision。读取持久约定、已记录的人类决定及子任务结果，不依赖聊天记忆。'
        '通过 CLI 原生工具在授权目录工作；文档工作只能写 docs/，审核只能只读，不允许自行提交、合并或发布。'
        '你在受限沙箱中运行，无法读取用户 Git 配置、仓库 .git 元数据、其他环境目录或本机服务；'
        '分支、HEAD 与未提交改动以上下文 workspace_state 为准（宿主本轮开始时读取），'
        '不要在沙箱内运行 env.sh status 或 git 核对环境，这类命令失败不构成阻塞。'
        '本轮必须用 shared_work 写结构化结果并通过 publish_message 给出简洁进度。'
        '首次接单可用 refine_agreement(data:{task_id,agreement:{criteria:[{id,text}],validation_commands:[[命令参数]]}}) '
        '把原始目标整理为可测试约定；不得改变 source/goal/scope/delivery，实质变更须请用户决定。'
        '可提交 submit；必须明确 kind 为 code、design 或 document：设计候选用 design，最终代码用 code，文档用 document。'
        '设计候选作者可以审核他人实现的最终代码；不能把设计交付误报成代码交付。'
        '尚有必要工作用 continue_work；需要人类选择用 request_decision；真正阻塞用 block。'
        '新写操作使用稳定 operation_id 与最新 expected_revision，冲突后重读。'
        '需要另一角色时只使用 invoke_agent，purpose 写清独立交付物；宿主保存子任务，父责任不解除。'
        '发现 security/data/migration/contract/governance 风险调用 raise_risk，不能自行免除必须审核。'
        '审核阶段对照原始约定、真实候选和证据，用 review 引用 candidate_id、criterion_ids、decision、note。'
        'handoff 阶段只读交接资料，目标角色确认能接手后调用 accept_transfer(data:{task_id})。'
        '不能用成功退出、聊天声明或模拟结果代替验证，不能为凑流程增加角色。\n'
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

