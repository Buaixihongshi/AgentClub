"""Stdio MCP server exposing AgentClub tools to a CLI provider.

Spawned by an MCP-aware CLI (e.g. Codex). Reads runtime context from env vars
set by `MCPSubprocessAdapter`, then handles JSON-RPC 2.0 over stdin/stdout.

Exposes tools for messages, artifacts, status, A2A and durable shared work. All
handlers are thin wrappers around `MCPTools`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from backend.app.mcp.tools import MCPToolError, MCPTools
from backend.app.storage.repository import Repository


PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "yiren-hub"
SERVER_VERSION = "0.1.0"

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


class _Context:
    __slots__ = ("mission_id", "agent_id", "turn_id", "provider_id", "tools")

    def __init__(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        tools: MCPTools,
    ) -> None:
        self.mission_id = mission_id
        self.agent_id = agent_id
        self.turn_id = turn_id
        self.provider_id = provider_id
        self.tools = tools


def _load_context() -> _Context:
    db_path = os.environ.get("YIREN_MCP_DB_PATH")
    artifact_root = os.environ.get("YIREN_MCP_ARTIFACT_ROOT")
    mission_id = os.environ.get("YIREN_MCP_MISSION_ID")
    agent_id = os.environ.get("YIREN_MCP_AGENT_ID")
    turn_id = os.environ.get("YIREN_MCP_TURN_ID")
    provider_id = os.environ.get("YIREN_MCP_PROVIDER_ID")
    missing = [
        name
        for name, value in [
            ("YIREN_MCP_DB_PATH", db_path),
            ("YIREN_MCP_ARTIFACT_ROOT", artifact_root),
            ("YIREN_MCP_MISSION_ID", mission_id),
            ("YIREN_MCP_AGENT_ID", agent_id),
            ("YIREN_MCP_PROVIDER_ID", provider_id),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(
            "MCP server missing required env vars: {names}".format(names=", ".join(missing))
        )

    # Optional: known agent IDs for target validation.
    valid_agents_raw = os.environ.get("YIREN_MCP_VALID_AGENTS", "")
    valid_agent_ids = (
        {a.strip() for a in valid_agents_raw.split(",") if a.strip()}
        if valid_agents_raw.strip()
        else None
    )

    # Optional: A2A budget limit for subprocess enforcement.
    max_a2a_raw = os.environ.get("YIREN_MCP_MAX_A2A_INVOCATIONS", "")
    receipt_id = os.environ.get("YIREN_MCP_RECEIPT_ID")
    try:
        max_a2a_invocations = int(max_a2a_raw) if max_a2a_raw.strip() else 0
    except ValueError:
        max_a2a_invocations = 0

    repository = Repository(Path(db_path))
    tools = MCPTools(
        repository,
        Path(artifact_root),
        valid_agent_ids=valid_agent_ids,
        max_a2a_invocations=max_a2a_invocations,
        receipt_id=receipt_id,
        lease_token=os.environ.get('YIREN_MCP_LEASE_TOKEN'),
        workspace_root=Path(os.environ.get('YIREN_MCP_WORKSPACE_ROOT', os.getcwd())),
    )
    return _Context(
        mission_id=mission_id,
        agent_id=agent_id,
        turn_id=turn_id or "unknown",
        provider_id=provider_id,
        tools=tools,
    )


_TOOL_DESCRIPTORS = [
    {
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
    },
    {
        "name": "publish_message",
        "description": (
            "把当前 agent 在本回合要说的话发布到 AgentClub 聊天室。"
            "调用一次即可发出一条消息，参数 content 为要发送的中文文本。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "本回合要发布的中文消息正文；按任务需要详略，不设置固定字数上限。",
                }
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_context",
        "description": (
            "读取当前会话的正式对话与当前 mission 元数据；无会话时仅读取当前 mission。"
            "优先保留最新用户输入和插话，再补充近期发言，按事件顺序返回正文全文。"
            "可选参数 limit 控制最多返回消息条数（默认 20，上限 100）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "最多返回的消息条数，默认 20，最大 100。",
                    "default": 20,
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_artifact",
        "description": (
            "向 artifacts 目录写入一个文件。relative_path 是相对于本 mission "
            "artifacts 目录的路径（不能以 ../ 跳出）。content 与 source_path 二选一。"
            "已有本地文件时优先传 source_path，宿主直接复制并登记，无需读取或重新输出全文。"
            "source_path 相对宿主工作区解析，也可传工作区内绝对路径，上限 10 MiB。"
            "保存后用户立即看到文件卡片；仍需 publish_message 说明结果。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "相对于本 mission artifacts 目录的文件路径，例如 'output.md'。",
                },
                "content": {
                    "type": "string",
                    "description": "写入文件的文本内容。",
                },
                "source_path": {
                    "type": "string",
                    "description": "已有文件的本地路径；相对路径基于宿主工作区，不是 CLI 临时目录。与 content 二选一。",
                },
            },
            "required": ["relative_path"],
            "oneOf": [{"required": ["content"]}, {"required": ["source_path"]}],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_artifact",
        "description": (
            "读取 artifacts 目录下的产物文件。relative_path 不能跳出该目录。"
            "默认读当前 mission；同一会话跨任务时传来源 source_mission_id。"
            "默认带行号返回（方便引用『你第 N 行说…』），如需原始文本传 with_line_numbers=false。"
            "单次最多返回 20000 字符，超出会截断并在结果里告知。"
            "建议在做『断言别人写了什么』之前先调一次，避免凭印象说错。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "相对于本 mission artifacts 目录的文件路径，例如 'plan.md' 或 'notes/draft.md'。",
                },
                "source_mission_id": {
                    "type": "string",
                    "description": "可选。同一会话中来源任务的 id；省略时读当前任务。",
                },
                "with_line_numbers": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否在每行前加行号；默认 true。",
                },
            },
            "required": ["relative_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_status",
        "description": (
            "更新当前 mission 的状态，可选附带一条摘要说明。"
            "status 必须是合法的 MissionStatus 枚举值；圆桌只允许报告 running，结束由宿主控制。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "running", "completed", "failed", "blocked"],
                    "description": "目标 mission 状态。",
                },
                "summary": {
                    "type": "string",
                    "description": "可选的状态摘要说明。",
                },
            },
            "required": ["status"],
            "additionalProperties": False,
        },
    },
    {
        "name": "invoke_agent",
        "description": (
            "请求另一个 AgentClub agent 接力处理子任务。"
            "支持 fire_and_forget 和 wait_reply；受邀者在当前 turn 结束后顺序发言。"
            "wait_reply 接纳后结束当前发言，宿主安排受邀者及你的续答，不会在本次工具调用中返回回复正文。"
            "圆桌仅可邀请本场参与者，共享总回合预算；accepted/scheduled 表示已接纳或已有安排，不表示已发言。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_agent_id": {
                    "type": "string",
                    "enum": ["chulan", "baobao", "wangye"],
                    "description": "目标 agent id。",
                },
                "purpose": {
                    "type": "string",
                    "description": "给被叫 agent 的中文接力说明。",
                },
                "mode": {
                    "type": "string",
                    "enum": ["fire_and_forget", "wait_reply"],
                    "default": "fire_and_forget",
                    "description": "fire_and_forget 安排接力；wait_reply 安排受邀者回复后恢复请求者。工具调用本身不返回对方的答案。",
                },
            },
            "required": ["target_agent_id", "purpose"],
            "additionalProperties": False,
        },
    },
    {
        "name": "log_thought",
        "description": (
            "记录当前 agent 的内心想法（思考过程）。"
            "这些想法不会直接发给用户，但会记录在任务日志中，供调试和后续参考。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "思考的具体内容。",
                },
                "kind": {
                    "type": "string",
                    "enum": ["reasoning", "plan", "doubt", "decision"],
                    "default": "reasoning",
                    "description": "思考类型，默认 reasoning。",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选的标签，用于对想法进行分类（如 'strategy', 'analysis'）。",
                },
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
]


def _handle_publish_message(ctx: _Context, arguments: Dict[str, Any]) -> Dict[str, Any]:
    content = arguments.get("content")
    if not isinstance(content, str) or not content.strip():
        raise MCPToolError("publish_message requires non-empty 'content' string")
    message = ctx.tools.publish_message(
        ctx.mission_id,
        ctx.agent_id,
        ctx.turn_id,
        content,
        ctx.provider_id,
    )
    return {
        "content": [
            {"type": "text", "text": "ok: {id}".format(id=message.id)},
        ],
        "isError": False,
    }


def _handle_read_context(ctx: _Context, arguments: Dict[str, Any]) -> Dict[str, Any]:
    limit = arguments.get("limit", 20)
    if not isinstance(limit, int) or limit < 1:
        limit = 20
    limit = min(limit, 100)
    result = ctx.tools.read_context(
        ctx.mission_id,
        limit,
        agent_id=ctx.agent_id,
        turn_id=ctx.turn_id,
        provider_id=ctx.provider_id,
        **{key: arguments[key] for key in ('material_offset', 'history_offset', 'issue_offset') if key in arguments},
    )
    return {
        "content": [
            {"type": "text", "text": _json_text(result)},
        ],
        "isError": False,
    }


def _handle_write_artifact(ctx: _Context, arguments: Dict[str, Any]) -> Dict[str, Any]:
    relative_path = arguments.get("relative_path")
    content = arguments.get("content")
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise MCPToolError("write_artifact requires non-empty 'relative_path' string")
    source_path = arguments.get("source_path")
    if ("content" in arguments) == ("source_path" in arguments):
        raise MCPToolError("write_artifact requires exactly one of content or source_path")
    if "content" in arguments and not isinstance(content, str):
        raise MCPToolError("write_artifact requires 'content' string")
    if "source_path" in arguments and (not isinstance(source_path, str) or not source_path.strip()):
        raise MCPToolError("write_artifact requires non-empty 'source_path' string")
    target = ctx.tools.write_artifact(
        ctx.mission_id,
        ctx.agent_id,
        ctx.turn_id,
        relative_path,
        content,
        provider_id=ctx.provider_id,
        source_path=source_path,
    )
    return {
        "content": [
            {"type": "text", "text": "ok: {path}".format(path=target.as_posix())},
        ],
        "isError": False,
    }


def _handle_read_artifact(ctx: _Context, arguments: Dict[str, Any]) -> Dict[str, Any]:
    relative_path = arguments.get("relative_path")
    with_line_numbers = arguments.get("with_line_numbers", True)
    source_mission_id = arguments.get("source_mission_id")
    if source_mission_id is not None and (
        not isinstance(source_mission_id, str) or not source_mission_id.strip()
    ):
        raise MCPToolError("read_artifact 'source_mission_id' must be a non-empty string")
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise MCPToolError("read_artifact requires non-empty 'relative_path' string")
    if not isinstance(with_line_numbers, bool):
        raise MCPToolError("read_artifact 'with_line_numbers' must be a boolean")
    result = ctx.tools.read_artifact(
        ctx.mission_id,
        ctx.agent_id,
        ctx.turn_id,
        relative_path,
        provider_id=ctx.provider_id,
        with_line_numbers=with_line_numbers,
        source_mission_id=source_mission_id,
        **{key: arguments[key] for key in ('offset', 'max_chars') if key in arguments},
    )
    return {
        "content": [
            {"type": "text", "text": _json_text(result)},
        ],
        "isError": False,
    }


def _handle_update_status(ctx: _Context, arguments: Dict[str, Any]) -> Dict[str, Any]:
    status = arguments.get("status")
    summary = arguments.get("summary")
    valid_statuses = {"pending", "running", "completed", "failed", "blocked"}
    if not isinstance(status, str) or status not in valid_statuses:
        raise MCPToolError(
            "update_status requires 'status' in {valid}".format(valid=sorted(valid_statuses))
        )
    if summary is not None and not isinstance(summary, str):
        raise MCPToolError("update_status 'summary' must be a string if provided")
    ctx.tools.update_status(
        ctx.mission_id,
        status,
        summary or None,
        agent_id=ctx.agent_id,
        turn_id=ctx.turn_id,
        provider_id=ctx.provider_id,
    )
    return {
        "content": [
            {"type": "text", "text": "ok: status={status}".format(status=status)},
        ],
        "isError": False,
    }


def _handle_invoke_agent(ctx: _Context, arguments: Dict[str, Any]) -> Dict[str, Any]:
    target_agent_id = arguments.get("target_agent_id")
    purpose = arguments.get("purpose")
    mode = arguments.get("mode", "fire_and_forget")
    if not isinstance(target_agent_id, str) or not target_agent_id.strip():
        raise MCPToolError("invoke_agent requires non-empty 'target_agent_id' string")
    if not isinstance(purpose, str) or not purpose.strip():
        raise MCPToolError("invoke_agent requires non-empty 'purpose' string")
    if not isinstance(mode, str):
        raise MCPToolError("invoke_agent 'mode' must be a string")
    result = ctx.tools.invoke_agent(
        ctx.mission_id,
        ctx.agent_id,
        ctx.turn_id,
        target_agent_id,
        purpose,
        ctx.provider_id,
        mode=mode,
    )
    return {
        "content": [
            {"type": "text", "text": _json_text(result)},
        ],
        "isError": False,
    }


def _handle_log_thought(ctx: _Context, arguments: Dict[str, Any]) -> Dict[str, Any]:
    content = arguments.get("content")
    kind = arguments.get("kind")
    tags = arguments.get("tags")
    result = ctx.tools.log_thought(
        ctx.mission_id,
        ctx.agent_id,
        ctx.turn_id,
        content,
        ctx.provider_id,
        kind=kind,
        tags=tags,
    )
    return {
        "content": [
            {"type": "text", "text": _json_text(result)},
        ],
        "isError": False,
    }


def _json_text(obj: Any) -> str:
    import json as _json
    return _json.dumps(obj, ensure_ascii=False, default=str)


def _handle_submit_discussion_result(ctx: _Context, arguments: Dict[str, Any]) -> Dict[str, Any]:
    result = ctx.tools.submit_discussion_result(ctx.mission_id, ctx.agent_id, ctx.turn_id,
                                              ctx.provider_id, arguments.get('result'))
    return {'content': [{'type': 'text', 'text': _json_text(result)}], 'isError': False}


def _handle_read_discussion_result(ctx: _Context, arguments: Dict[str, Any]) -> Dict[str, Any]:
    result = ctx.tools.read_discussion_result(ctx.mission_id, ctx.agent_id, ctx.turn_id, ctx.provider_id,
        arguments.get('source_id'), arguments.get('offset', 0), arguments.get('max_chars', 8000))
    return {'content': [{'type': 'text', 'text': _json_text(result)}], 'isError': False}


def _handle_shared_work(ctx: _Context, arguments: Dict[str, Any]) -> Dict[str, Any]:
    result = ctx.tools.shared_work(ctx.mission_id, ctx.agent_id, ctx.turn_id, ctx.provider_id,
        arguments.get('action', 'read'), arguments.get('data'), arguments.get('expected_revision'), arguments.get('operation_id'))
    return {'content': [{'type': 'text', 'text': _json_text(result)}], 'isError': False}


_TOOL_HANDLERS: Dict[str, Callable[[_Context, Dict[str, Any]], Dict[str, Any]]] = {
    'shared_work': _handle_shared_work,
    "read_discussion_result": _handle_read_discussion_result,
    "submit_discussion_result": _handle_submit_discussion_result,
    "publish_message": _handle_publish_message,
    "read_context": _handle_read_context,
    "write_artifact": _handle_write_artifact,
    "read_artifact": _handle_read_artifact,
    "update_status": _handle_update_status,
    "invoke_agent": _handle_invoke_agent,
    "log_thought": _handle_log_thought,
}


def _ok(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _handle_initialize(_ctx: _Context, _params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "capabilities": {"tools": {"listChanged": False}},
    }


def _handle_tools_list(ctx: _Context, _params: Dict[str, Any]) -> Dict[str, Any]:
    from copy import deepcopy
    descriptors = deepcopy(_TOOL_DESCRIPTORS)
    mission = ctx.tools.repository.get_mission(ctx.mission_id)
    from backend.app.orchestration.deliberation import is_v2
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
        return {'tools': descriptors}
    if mission and mission.room_mode == "roundtable" and mission.discussion:
        for descriptor in descriptors:
            properties = descriptor["inputSchema"]["properties"]
            if descriptor["name"] == "invoke_agent":
                properties["target_agent_id"]["enum"] = mission.discussion.participant_ids
            if descriptor["name"] == "update_status":
                properties["status"]["enum"] = ["running"]
    return {"tools": descriptors}


def _handle_tools_call(ctx: _Context, params: Dict[str, Any]) -> Dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise _RpcError(JSONRPC_INVALID_PARAMS, "tools/call requires 'name' (str) and 'arguments' (object)")
    from backend.app.orchestration.deliberation import is_v2
    if is_v2(ctx.tools.repository.get_mission(ctx.mission_id)) and name not in {'read_context', 'read_artifact', 'read_discussion_result', 'submit_discussion_result'}:
        return {'content': [{'type': 'text', 'text': 'Tool is unavailable in V2 discussion'}], 'isError': True}
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        raise _RpcError(JSONRPC_METHOD_NOT_FOUND, "Unknown tool: {name}".format(name=name))
    try:
        return handler(ctx, arguments)
    except MCPToolError as exc:
        return {
            "content": [{"type": "text", "text": str(exc)}],
            "isError": True,
        }


_REQUEST_HANDLERS: Dict[str, Callable[[_Context, Dict[str, Any]], Dict[str, Any]]] = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
}


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _dispatch(ctx: _Context, frame: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = frame.get("method")
    request_id = frame.get("id")
    is_notification = "id" not in frame
    params = frame.get("params") or {}

    if not isinstance(method, str):
        if is_notification:
            return None
        return _err(request_id, JSONRPC_INVALID_REQUEST, "Missing 'method'")

    if method.startswith("notifications/"):
        return None

    handler = _REQUEST_HANDLERS.get(method)
    if handler is None:
        if is_notification:
            return None
        return _err(request_id, JSONRPC_METHOD_NOT_FOUND, "Method not found: {m}".format(m=method))

    try:
        result = handler(ctx, params if isinstance(params, dict) else {})
    except _RpcError as exc:
        return _err(request_id, exc.code, exc.message)
    except Exception as exc:  # pragma: no cover - defensive
        return _err(request_id, JSONRPC_INTERNAL_ERROR, str(exc))
    return _ok(request_id, result)


def run(stdin=None, stdout=None, stderr=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        ctx = _load_context()
    except RuntimeError as exc:
        stderr.write(str(exc) + "\n")
        stderr.flush()
        return 2

    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _err(None, JSONRPC_PARSE_ERROR, "Invalid JSON: {e}".format(e=exc))
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
            continue
        if not isinstance(frame, dict):
            response = _err(None, JSONRPC_INVALID_REQUEST, "Frame must be a JSON object")
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
            continue
        response = _dispatch(ctx, frame)
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(run())
