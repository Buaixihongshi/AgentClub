"""Discussion MCP tools: roundtable scope, frozen V2 reads and structured results."""
from backend.app.domain.errors import DiscussionError
from backend.app.mcp.tools import MCPToolError, _clip_text
from backend.app.orchestration.events import record_tool_call, record_tool_error, record_tool_result


def _context_digest(view: dict) -> str:
    """A readable summary of the frozen context an agent just read.

    The raw view is mostly ids; the useful part is the task, the other agents'
    positions, the open issues and which materials are in play. This is what
    the observability panel shows instead of "Returned frozen phase context".
    """
    snapshot = view.get("snapshot") if isinstance(view.get("snapshot"), dict) else {}
    results = view.get("results") if isinstance(view.get("results"), list) else []
    issues = view.get("issues") if isinstance(view.get("issues"), dict) else {}
    materials = snapshot.get("materials") if isinstance(snapshot.get("materials"), dict) else {}
    paths = [str(item.get("path")) for item in materials.values()
             if isinstance(item, dict) and item.get("path")]
    lines = ["phase={phase} · round={round} · results={r} · issues={i} · materials={m} · history={h}".format(
        phase=view.get("phase") or "?",
        round=view.get("round_index", 0),
        r=len(results), i=len(issues), m=len(materials),
        h=snapshot.get("history_count") or 0)]
    task = snapshot.get("goal")
    if task:
        lines.append("任务：" + _clip_text(task, 140))
    for result in results[:3]:
        if not isinstance(result, dict):
            continue
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        text = data.get("position_summary") or data.get("content") or ""
        if not text:
            claims = data.get("claims") if isinstance(data.get("claims"), list) else []
            text = claims[0].get("statement") if claims and isinstance(claims[0], dict) else ""
        if not text:
            sections = data.get("sections") if isinstance(data.get("sections"), list) else []
            text = sections[0].get("content") if sections and isinstance(sections[0], dict) else ""
        if text:
            lines.append("- {agent}/{kind}: {text}".format(
                agent=result.get("agent_id") or "?", kind=result.get("kind") or "?",
                text=_clip_text(text, 140)))
    for issue in list(issues.values())[:2]:
        if isinstance(issue, dict):
            lines.append("- issue/{cat}: {problem}".format(
                cat=issue.get("category") or "?", problem=_clip_text(issue.get("problem"), 120)))
    if paths:
        shown = "、".join(paths[:3]) + ("…" if len(paths) > 3 else "")
        lines.append("材料：" + shown)
    return "\n".join(lines)


def roundtable_guard(tools, name, values, call):
    """Run a core tool inside the roundtable turn scope; V2 forbids it outright."""
    from backend.app.extensions.discussion.roundtable import Roundtable
    mission_id = values.get('mission_id')
    mission = tools.repository.get_mission(mission_id)
    agent = values.get('agent_id') or values.get('sender_agent_id')
    turn = values.get('turn_id') or values.get('sender_turn_id')
    if mission is None or mission.room_mode != 'roundtable' or (name == 'update_status' and not agent):
        return call()
    if mission.discussion and mission.discussion.protocol == 'deliberation_v2':
        raise MCPToolError('V2 discussion only allows frozen context/source reads and structured submission')
    try:
        with Roundtable(tools.repository).tool_scope(mission_id, agent, turn, values.get('provider_id')):
            if name == 'update_status' and values.get('status') != 'running':
                raise DiscussionError('The host owns roundtable lifecycle; publish your conclusion as a message')
            return call()
    except (DiscussionError, MCPToolError) as exc:
        record_tool_error(tools.repository, mission_id, turn or 'unknown', agent or 'unknown', name, str(exc), values.get('provider_id'))
        raise MCPToolError(str(exc)) from exc


def override(tools, name, values):
    """V2 reads come from the frozen phase snapshot; roundtable invites use its policy."""
    if name == 'read_context':
        return _read_context(tools, **values)
    if name == 'read_artifact':
        return _read_artifact(tools, **values)
    if name == 'invoke_agent' and values['mission'].room_mode == 'roundtable':
        return _invoke_agent(tools, **values)
    return None


def _read_context(tools, mission_id, agent_id, turn_id, provider_id, material_offset, history_offset, issue_offset):
    from backend.app.extensions.discussion.deliberation import Deliberation, is_v2
    if is_v2(tools.repository.get_mission(mission_id)):
        try:
            result = Deliberation(tools.repository).read_context(mission_id, agent_id, turn_id, provider_id, tools.lease_token,
                material_offset=material_offset, history_offset=history_offset, issue_offset=issue_offset)
            record_tool_call(tools.repository, mission_id, turn_id, agent_id, 'read_context', {}, provider_id)
            record_tool_result(tools.repository, mission_id, turn_id, agent_id, 'read_context',
                               summary='Returned frozen phase context', provider_id=provider_id,
                               output=_context_digest(result))
            return result
        except ValueError as exc:
            raise MCPToolError(str(exc)) from exc
    return None


def _read_artifact(tools, mission_id, agent_id, turn_id, relative_path, provider_id, source_mission_id, offset, max_chars):
    from backend.app.extensions.discussion.deliberation import is_v2
    if is_v2(tools.repository.get_mission(mission_id)):
        from backend.app.extensions.discussion.deliberation import Deliberation
        key = f'artifact:{source_mission_id or mission_id}:{relative_path}'
        record_tool_call(
            tools.repository, mission_id, turn_id, agent_id, "read_artifact",
            {"relative_path": relative_path, "offset": offset}, provider_id,
        )
        try:
            page = Deliberation(tools.repository).read_material(
                mission_id, agent_id, turn_id, provider_id, tools.lease_token, key, offset, max_chars)
        except ValueError as exc:
            record_tool_error(tools.repository, mission_id, turn_id, agent_id,
                              "read_artifact", str(exc), provider_id)
            raise MCPToolError(str(exc)) from exc
        content = page.get("content") if isinstance(page.get("content"), str) else ""
        record_tool_result(
            tools.repository, mission_id, turn_id, agent_id, "read_artifact",
            summary="Read frozen material {p}".format(p=relative_path),
            provider_id=provider_id, output=content,
        )
        return page
    return None


def _invoke_agent(tools, mission, mission_id, sender_agent_id, sender_turn_id, target_agent_id, purpose, provider_id, mode):
    from backend.app.extensions.discussion.roundtable import Roundtable
    record_tool_call(tools.repository, mission_id, sender_turn_id, sender_agent_id, 'invoke_agent', {'target_agent_id': target_agent_id, 'purpose': purpose, 'mode': mode}, provider_id)
    result = Roundtable(tools.repository).invoke(mission_id, sender_agent_id, sender_turn_id, target_agent_id, purpose, mode)
    record_tool_result(tools.repository, mission_id, sender_turn_id, sender_agent_id, 'invoke_agent', summary=result['status'], provider_id=provider_id)
    return result


def read_discussion_result(tools, mission_id, agent_id, turn_id, provider_id, source_id, offset=0, max_chars=8000):
    from backend.app.extensions.discussion.deliberation import Deliberation
    try:
        result = Deliberation(tools.repository).read_entry(mission_id, agent_id, turn_id, provider_id,
            tools.lease_token, source_id, offset, max_chars)
        record_tool_call(tools.repository, mission_id, turn_id, agent_id, 'read_discussion_result',
                         {'source_id': source_id, 'offset': offset}, provider_id)
        page = result.get('text') if isinstance(result, dict) else None
        record_tool_result(tools.repository, mission_id, turn_id, agent_id, 'read_discussion_result',
                           summary='Returned frozen source page', provider_id=provider_id,
                           output=page if isinstance(page, str) else None)
        return result
    except ValueError as exc:
        raise MCPToolError(str(exc)) from exc

def submit_discussion_result(tools, mission_id, agent_id, turn_id, provider_id, result):
    from backend.app.extensions.discussion.deliberation import Deliberation
    kind = result.get('kind') if isinstance(result, dict) else 'invalid'
    if not isinstance(kind, str) or kind not in {'opinion', 'contribution', 'review', 'draft'}:
        kind = 'invalid'
    record_tool_call(tools.repository, mission_id, turn_id, agent_id, 'submit_discussion_result', {'kind': kind}, provider_id)
    try:
        submitted = Deliberation(tools.repository).submit(mission_id, agent_id, turn_id, provider_id, tools.lease_token, result)
        record_tool_result(tools.repository, mission_id, turn_id, agent_id, 'submit_discussion_result', summary='Structured result accepted', provider_id=provider_id)
        return submitted
    except ValueError as exc:
        raise MCPToolError(str(exc)) from exc


_V2_TOOLS = {'read_context', 'read_artifact', 'read_discussion_result', 'submit_discussion_result'}


def mcp_tools_list(tools, mission, descriptors):
    from backend.app.extensions.discussion.deliberation import is_v2
    if is_v2(mission):
        descriptors = [d for d in descriptors if d['name'] in {'read_context', 'read_artifact'}]
        for descriptor in descriptors:
            properties = descriptor['inputSchema']['properties']
            names = ('material_offset', 'history_offset', 'issue_offset') if descriptor['name'] == 'read_context' else ('offset', 'max_chars')
            for key in names:
                properties[key] = {'type': 'integer', 'minimum': 1 if key == 'max_chars' else 0}
                if key == 'max_chars':
                    properties[key]['maximum'] = 8000
        descriptors.append({'name': 'read_discussion_result',
            'description': '按 ID 分段读取本阶段可见的原始观点、历史消息、分歧或综合稿；truncated 条目必须完整读取。',
            'inputSchema': {'type': 'object', 'properties': {
                'source_id': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0},
                'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 8000}},
                'required': ['source_id'], 'additionalProperties': False}})
        descriptors.append({
            'name': 'submit_discussion_result',
            'description': '提交当前阶段的结构化结果。schema 在本轮 prompt 中；成功后结束本轮。不能用普通聊天代替。',
            'inputSchema': {'type': 'object', 'properties': {'result': {'type': 'object'}},
                            'required': ['result'], 'additionalProperties': False},
        })
        return descriptors
    if mission and mission.room_mode == "roundtable" and mission.discussion:
        for descriptor in descriptors:
            properties = descriptor["inputSchema"]["properties"]
            if descriptor["name"] == "invoke_agent":
                properties["target_agent_id"]["enum"] = mission.discussion.participant_ids
            if descriptor["name"] == "update_status":
                properties["status"]["enum"] = ["running"]
    return descriptors


def mcp_call_refusal(tools, mission, name):
    from backend.app.extensions.discussion.deliberation import is_v2
    if is_v2(mission) and name not in _V2_TOOLS:
        return 'Tool is unavailable in V2 discussion'
    return None


def _handle_submit_discussion_result(ctx, arguments):
    from backend.app.mcp.server import _json_text
    result = submit_discussion_result(ctx.tools, ctx.mission_id, ctx.agent_id, ctx.turn_id,
                                      ctx.provider_id, arguments.get('result'))
    return {'content': [{'type': 'text', 'text': _json_text(result)}], 'isError': False}


def _handle_read_discussion_result(ctx, arguments):
    from backend.app.mcp.server import _json_text
    result = read_discussion_result(ctx.tools, ctx.mission_id, ctx.agent_id, ctx.turn_id, ctx.provider_id,
        arguments.get('source_id'), arguments.get('offset', 0), arguments.get('max_chars', 8000))
    return {'content': [{'type': 'text', 'text': _json_text(result)}], 'isError': False}


MCP_HANDLERS = {
    'read_discussion_result': _handle_read_discussion_result,
    'submit_discussion_result': _handle_submit_discussion_result,
}
