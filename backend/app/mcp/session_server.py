"""Stable stdio transport with a host-issued capability on every tool call."""
from copy import deepcopy
import json
import os
import sys
from pathlib import Path

from backend.app.mcp import server
from backend.app.mcp.tools import MCPTools, MCPToolError
from backend.app.storage.execution import ExecutionLedger
from backend.app.storage.repository import Repository

MUTATIONS = {'publish_message', 'write_artifact', 'update_status', 'invoke_agent', 'log_thought'}


def dispatch(repo, root, worker_id, frame):
    request_id = frame.get('id')
    method = frame.get('method')
    if isinstance(method, str) and method.startswith('notifications/'):
        return None
    if method == 'initialize':
        return server._ok(request_id, server._handle_initialize(None, {}))
    if method == 'tools/list':
        descriptors = deepcopy(server._TOOL_DESCRIPTORS)
        for item in descriptors:
            schema = item['inputSchema']
            schema['properties']['context_token'] = {'type': 'string', 'description': '本轮宿主签发的 context_token；不得沿用上一轮。'}
            schema.setdefault('required', []).append('context_token')
            if item['name'] in MUTATIONS:
                schema['properties']['operation_id'] = {'type': 'string', 'description': '本轮操作的稳定编号；同一操作重试须复用编号。'}
                schema['required'].append('operation_id')
            if item['name'] == 'update_status':
                schema['properties']['status']['enum'] = ['running']
        return server._ok(request_id, {'tools': descriptors})
    if method != 'tools/call':
        return server._err(request_id, server.JSONRPC_METHOD_NOT_FOUND, 'Unknown method')
    params = frame.get('params')
    if not isinstance(params, dict) or not isinstance(params.get('arguments'), dict):
        return server._err(request_id, server.JSONRPC_INVALID_PARAMS, 'Expected tool arguments')
    name, args = params.get('name'), dict(params['arguments'])
    if not isinstance(name, str) or name not in server._TOOL_HANDLERS:
        return server._err(request_id, server.JSONRPC_METHOD_NOT_FOUND, 'Unknown tool')
    token = args.pop('context_token', None)
    operation_id = args.pop('operation_id', None)
    try:
        if name in MUTATIONS and operation_id is None:
            raise MCPToolError('Mutation requires a stable operation_id')
        if name == 'update_status' and args.get('status') != 'running':
            raise MCPToolError('The host owns task lifecycle; publish a conclusion instead')
        def invoke(grant):
            metadata = json.loads(grant['data'])
            tools = MCPTools(repo, root, receipt_id=grant['receipt_id'],
                             valid_agent_ids=set(metadata.get('valid_agent_ids') or []),
                             max_a2a_invocations=metadata.get('max_a2a_invocations', 0),
                             workspace_root=Path(os.environ.get('YIREN_MCP_WORKSPACE_ROOT', os.getcwd())))
            ctx = server._Context(grant['mission_id'], grant['agent_id'], grant['turn_id'], grant['provider_id'], tools)
            return server._TOOL_HANDLERS[name](ctx, args)
        result = ExecutionLedger(repo).call(worker_id, token, name, args, operation_id, invoke)
        return server._ok(request_id, result)
    except (MCPToolError, ValueError) as exc:
        return server._ok(request_id, {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True})


def run():
    repo = Repository(Path(os.environ['YIREN_MCP_DB_PATH']))
    root = Path(os.environ['YIREN_MCP_ARTIFACT_ROOT'])
    worker_id = os.environ['YIREN_MCP_WORKER_ID']
    for line in sys.stdin:
        try:
            frame = json.loads(line)
            if not isinstance(frame, dict):
                raise ValueError('Expected JSON object')
            response = dispatch(repo, root, worker_id, frame)
        except (ValueError, TypeError) as exc:
            response = server._err(None, server.JSONRPC_PARSE_ERROR, str(exc))
        except Exception:
            response = server._err(None, server.JSONRPC_INTERNAL_ERROR, 'Tool execution failed')
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(run())
