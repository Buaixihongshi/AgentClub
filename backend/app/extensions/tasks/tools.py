"""Tasks MCP tools: the shared_work tool and adaptive-work limits on core tools."""
from backend.app.domain.models import is_provider_verification_mission
from backend.app.mcp.tools import MCPToolError, extension_guard
from backend.app.orchestration.events import record_tool_call, record_tool_result


@extension_guard
def shared_work(tools, mission_id, agent_id, turn_id, provider_id, action='read',
                data=None, expected_revision=None, operation_id=None):
    from backend.app.extensions.tasks.models import WorkCommand
    from backend.app.extensions.tasks.service import SharedWork, WorkError
    from pydantic import ValidationError
    from backend.app.settings import EXTENSION_DISABLED
    if not tools.tasks_enabled:
        raise MCPToolError(EXTENSION_DISABLED['tasks'])
    mission = tools._require_mission(mission_id)
    if not mission.conversation_id or is_provider_verification_mission(mission):
        raise MCPToolError('共享开发任务需要普通会话上下文')
    service = SharedWork(tools.repository, tools.workspace_root)
    try:
        if action == 'read':
            board = service.read(mission.conversation_id)
            if board.get('protocol') == 'adaptive_v1' and any(t['active_mission_id'] == mission_id for t in board['tasks']):
                from backend.app.extensions.tasks.adaptive import AdaptiveWork
                return AdaptiveWork(tools.repository, tools.workspace_root).agent_view(mission_id)
            return board
        if action == 'read_evidence':
            value = data or {}
            offset = value.get('offset', 0)
            if not isinstance(offset, int) or offset < 0:
                raise MCPToolError('证据分页 offset 无效')
            raw = service.read_log(mission.conversation_id, value.get('log_id')).decode(errors='replace')
            return {'content': raw[offset:offset + 8000], 'next_offset': offset + 8000 if offset + 8000 < len(raw) else None}
        command = WorkCommand.model_validate({'action': action, 'data': data or {}, 'expected_revision': expected_revision})
        record_tool_call(tools.repository, mission_id, turn_id, agent_id, 'shared_work',
                         {'action': action, 'task_id': command.data.get('task_id')}, provider_id)
        result = service.command(mission.conversation_id, action, command.data, expected_revision,
                                 actor=agent_id, mission_id=mission_id, turn_id=turn_id, operation_id=operation_id)
        record_tool_result(tools.repository, mission_id, turn_id, agent_id, 'shared_work',
                           summary=action, provider_id=provider_id)
        return result
    except (WorkError, ValidationError) as exc:
        raise MCPToolError(str(exc)) from exc


def tool_check(tools, name, values):
    """Adaptive work owns its lifecycle; read-only stages cannot write artifacts."""
    from backend.app.extensions.tasks.service import SharedWork
    mission_id = values['mission_id']
    if name == 'write_artifact':
        work = SharedWork(tools.repository, tools.workspace_root).execution_context(mission_id)
        if work and (work['task']['status'] == 'cancelled'
                     or work['task']['attempts'][-1]['stage'] in {'review', 'reconcile', 'handoff'}):
            raise MCPToolError('当前工作动作只读或已取消，不能写入产物')
    elif name == 'update_status':
        if values['agent_id'] and values['status'] != 'running' and SharedWork(tools.repository, tools.workspace_root).execution_context(mission_id):
            raise MCPToolError('自主工作的生命周期由宿主控制，请提交结构化结果')


def override(tools, name, values):
    """Inside adaptive work, invoke_agent records a durable delegated subtask."""
    if name != 'invoke_agent':
        return None
    return _delegate(tools, **values)


def _delegate(tools, mission, mission_id, sender_agent_id, sender_turn_id, target_agent_id, purpose, provider_id, mode):
    from backend.app.extensions.tasks.service import SharedWork, WorkError
    work_context = SharedWork(tools.repository, tools.workspace_root).execution_context(mission_id)
    if work_context:
        from backend.app.extensions.tasks.adaptive import AdaptiveWork, digest
        try:
            return AdaptiveWork(tools.repository, tools.workspace_root).delegate(
                mission.conversation_id, mission_id, sender_agent_id, sender_turn_id,
                target_agent_id, purpose, mode,
                digest({'mission': mission_id, 'turn': sender_turn_id, 'target': target_agent_id, 'purpose': purpose, 'mode': mode}))
        except WorkError as exc:
            raise MCPToolError(str(exc)) from exc
    return None


def context_extras(tools, mission, conversation_id):
    if tools.tasks_enabled:
        from backend.app.extensions.tasks.service import SharedWork
        return {'shared_work': SharedWork(tools.repository).read(conversation_id)}
    return None


SHARED_WORK_DESCRIPTOR = {
    'name': 'shared_work',
    'description': '持久自主工作。先 read 获取 revision、工作约定和证据。普通对话澄清需求后，用 propose_work(data:{title,owner_id,agreement:{source,goal,scope,non_goals?,delivery:document|development|online,criteria:[{id,text}],validation_commands?:[],risks?:[local]}}) 保存待用户确认的提议。它不会创建可执行任务或授权；仅用户确认后启动。只要方案用 document，开发才用 development，上线仍须独立版本审批。新协议 adaptive_v1：progress/continue_work(data:{task_id,text,next_action,evidence_ids?:[]}) 保存检查点；submit(data:{task_id,kind:code|design|document,summary,artifact_paths:[相对路径],observations:[{id:验收项ID,text:实际观察}],limitations?,reviewer_id?}) 提交产物，kind 必填：设计候选用 design，最终代码用 code，文档用 document，宿主采集真实验证；request_decision(data:{task_id,question,options:[{id,text}],kind:design|direction,artifact_paths:[],recommendation}) 请求必要决定；raise_risk(data:{task_id,risks:[security|data|migration|contract|governance]}) 增加风险；block(data:{task_id,reason,waiting_for}) 记录阻塞。需要协作仍用 invoke_agent，系统持久化子任务。独立审核 review(data:{task_id,candidate_id,criterion_ids,decision:approved|changes_requested,note})。不能自审或自行扩大授权。写入携带稳定 operation_id 与最新 expected_revision。旧协议才使用 propose_contract/create_task 和 commit_sha 审核。',
    'inputSchema': {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'enum': ['read', 'read_evidence', 'propose_work', 'propose_contract', 'create_task', 'progress', 'submit', 'review', 'block', 'continue_work', 'request_decision', 'raise_risk', 'propose_revision', 'propose_transfer', 'refine_agreement', 'accept_transfer']},
            'expected_revision': {'type': 'integer', 'minimum': 0},
            'operation_id': {'type': 'string', 'minLength': 1, 'maxLength': 120},
            'data': {'type': 'object'},
        },
        'required': ['action'],
        'additionalProperties': False,
    },
}


def mcp_tools_list(tools, mission, descriptors):
    if not getattr(tools, "tasks_enabled", True):
        descriptors = [d for d in descriptors if d["name"] != "shared_work"]
    return descriptors


def _handle_shared_work(ctx, arguments):
    from backend.app.mcp.server import _json_text
    result = shared_work(ctx.tools, ctx.mission_id, ctx.agent_id, ctx.turn_id, ctx.provider_id,
        arguments.get('action', 'read'), arguments.get('data'), arguments.get('expected_revision'), arguments.get('operation_id'))
    return {'content': [{'type': 'text', 'text': _json_text(result)}], 'isError': False}


MCP_HANDLERS = {'shared_work': _handle_shared_work}
