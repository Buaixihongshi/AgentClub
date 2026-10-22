"""Text-only API adapter. It never exposes terminal/file tools to the model."""
import json

from backend.app.adapters.api_transport import APIError, stream_text
from backend.app.adapters.base import CLIAdapter, TurnOutcome
from backend.app.domain.models import TurnStatus
from backend.app.mcp.tools import MCPToolError
from backend.app.orchestration.live_progress import get_live_progress_registry
from backend.app.storage.api_connections import read_key


class APITextAdapter(CLIAdapter):
    def __init__(self, settings, connection, model):
        self.settings = settings
        self.connection = dict(connection)
        self.model = model

    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        from backend.app.orchestration.deliberation import Deliberation, is_v2
        mission = tools.repository.get_mission(mission_id)
        structured = is_v2(mission)
        live = get_live_progress_registry()
        chunks = []
        emitted = False

        def cancelled():
            current = tools.repository.get_mission(mission_id)
            if current is None or current.status not in {'pending', 'running'}:
                return True
            if structured:
                state = Deliberation(tools.repository).state(current)
                return state['pending_control'] == 'stop' or state['phase'] == 'ended'
            if current.room_mode == 'roundtable':
                from backend.app.orchestration.roundtable import state_from_events
                state = state_from_events(current, tools.repository.list_events_after(mission_id, 0))
                return state['pending_control'] == 'stop' or state['phase'] == 'ended'
            return False

        try:
            # Capture once, before I/O. Rotation retires disk refs but cannot
            # change credentials of a request already in flight. A subsequent
            # turn with a retired snapshot ref fails closed (no silent rekey).
            key = read_key(self.settings, self.connection)
            if not key or not self.model.strip():
                raise APIError('configuration')
            if cancelled():
                raise APIError('stopped')
            if structured:
                policy = Deliberation(tools.repository)
                view = tools.read_context(mission_id, agent_id=agent_id, turn_id=turn_id, provider_id=provider_id)
                # Host materializes only the phase-visible, frozen inputs. The
                # normal read ledger and submission validator stay authoritative.
                with policy.transaction(mission_id) as (current, state):
                    lease = state['leases'][turn_id]
                    full = policy.visible(current, state, lease)
                ids = set(view['required_read_ids']) | {m['id'] for m in full['snapshot']['messages']}
                for source_id in ids:
                    offset = 0
                    while True:
                        page = tools.read_discussion_result(mission_id, agent_id, turn_id, provider_id, source_id, offset)
                        if page['next_offset'] is None:
                            break
                        offset = page['next_offset']
                for name in full['snapshot']['materials']:
                    offset = 0
                    while True:
                        page = policy.read_material(mission_id, agent_id, turn_id, provider_id, tools.lease_token, name, offset)
                        if page['next_offset'] is None:
                            break
                        offset = page['next_offset']
                from backend.app.domain.deliberation import RESULT_TYPES
                schema = RESULT_TYPES[full['phase']].model_json_schema()
                from backend.app.orchestration.deliberation_runner import PURPOSES
                prompt = ('你是纯文本讨论参与者，无文件写入、终端或工具执行能力。宿主已提供完整冻结输入。'
                          '仅返回符合 JSON schema 的单个 JSON 对象，不输出 Markdown 或工具调用。\n'
                          + PURPOSES[full['phase']] + '\n角色与讨论规则：\n' + prompt.split('【本轮协议】')[0]
                          + '\nJSON schema:\n' + json.dumps(schema, ensure_ascii=False)
                          + '\n冻结输入：\n' + json.dumps(full, ensure_ascii=False))
            else:
                prompt = ('你通过纯文本 API 参与聊天。你没有终端、文件读写、工具调用或邀请其他 Agent 的能力。'
                          '只能回答、分析与建议；执行类请求须明确说明能力限制，不得声称已修改或验证代码。\n' + prompt
                          + '\n【API 回复协议】上文的 CLI、MCP 工具调用要求不适用本连接。直接返回纯文本回复，宿主负责发布。不得声称执行过工具。')
            # Stream with a secret-sized tail so an upstream echo split between
            # SSE frames cannot leak the authentication value into live progress.
            pending = ''
            def receive(text):
                nonlocal emitted, pending
                emitted = True
                chunks.append(text)
                if not structured:
                    pending = (pending + text).replace(key, '[redacted]')
                    n = max(0, len(pending) - len(key) + 1)
                    if n:
                        live.append_text(mission_id, turn_id, pending[:n])
                        pending = pending[n:]
            if not structured:
                live.mark_active(mission_id, turn_id)
            stream_text(self.connection, key, self.model, prompt, receive, cancelled)
            if cancelled():
                raise APIError('stopped')
            text = ''.join(chunks).replace(key, '[redacted]').strip()
            if not text:
                raise APIError('empty_response')
            if structured:
                try:
                    result = json.loads(text)
                    tools.submit_discussion_result(mission_id, agent_id, turn_id, provider_id, result)
                except (ValueError, TypeError, MCPToolError):
                    raise APIError('invalid_submission') from None
            else:
                tools.publish_message(mission_id, agent_id, turn_id, text, provider_id)
            return TurnOutcome(status=TurnStatus.COMPLETED, summary='API text response accepted', output_started=emitted)
        except APIError as exc:
            return TurnOutcome(status=TurnStatus.BLOCKED, summary=str(exc), failure_code=exc.code,
                               retryable=exc.retryable and not emitted, output_started=emitted)
        except Exception:
            # Never persist raw upstream bodies, keys, URLs or arbitrary errors.
            return TurnOutcome(status=TurnStatus.BLOCKED, summary='API request failed: internal',
                               failure_code='configuration', retryable=False, output_started=emitted)
        finally:
            live.clear(mission_id, turn_id)
