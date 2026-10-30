#!/usr/bin/env python3
"""Roundtable HTTP acceptance, offline by default; --real explicitly uses Codex MCP."""
import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from backend.app.adapters.base import TurnOutcome
from backend.app.domain.models import TurnStatus
from backend.app.main import create_app
from backend.app.settings import Settings


class DiscussionScript:
    def __init__(self):
        self.ready = threading.Event()
        self.release = threading.Event()
        self.count = 0

    def run_turn(self, mid, agent, turn, provider, prompt, tools):
        self.count += 1
        if self.count == 1:
            content = '我建议先保障共享上下文，再做讨论界面。'
        elif self.count == 2:
            assert '先保障共享上下文' in prompt
            content = '我认可先保障共享上下文，还要验证插话会不会丢失。'
        elif '阶段：closing' in prompt:
            assert '只有一周开发时间' in prompt
            content = '共识：一周内先完成可靠讨论；分歧：界面优化的优先级；待验证：真实 CLI 的讨论质量。'
        else:
            assert '只有一周开发时间' in prompt
            content = '根据只有一周开发时间这个条件，我修正范围：先完成最小讨论闭环。'
        tools.publish_message(mid, agent, turn, content, provider)
        if self.count == 2:
            self.ready.set()
            assert self.release.wait(timeout=10), 'User interruption did not arrive'
        return TurnOutcome(status=TurnStatus.COMPLETED)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--fake', action='store_true')
    mode.add_argument('--real', action='store_true', help='Use authenticated Codex CLI through isolated stdio MCP; incurs model usage')
    parser.add_argument('--timeout-seconds', type=int, default=300)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='agentclub-roundtable-') as directory:
        temp = Path(directory)
        config_dir = ROOT / 'config'
        adapter = None if args.real else DiscussionScript()
        if args.real:
            config_dir = temp / 'config'
            config_dir.mkdir()
            providers = json.loads((ROOT / 'config/cli-providers.json').read_text())
            for provider in providers:
                provider['enabled'] = provider['id'] == 'codex'
                provider['mcp_enabled'] = provider['id'] == 'codex'
            (config_dir / 'cli-providers.json').write_text(json.dumps(providers))
            agents = json.loads((ROOT / 'config/agents.json').read_text())
            for agent in agents['agents']:
                agent['default_provider_id'] = 'codex'
                agent['fallback_provider_ids'] = []
            (config_dir / 'agents.json').write_text(json.dumps(agents, ensure_ascii=False))
        settings = Settings(database_path=temp / 'hub.sqlite3', artifact_root=temp / 'artifacts',
                            config_dir=config_dir, use_fake_cli=not args.real,
                            # Roundtables belong to the optional discussion extension.
                            discussion_enabled=True)
        with TestClient(create_app(settings, mission_adapter=adapter)) as client:
            response = client.post('/api/missions', json={
                'goal': 'Club 下一步该做什么？请讨论优先级，每次发言简洁并回应具体观点。',
                'room_mode': 'roundtable', 'request_id': 'smoke-start',
                'discussion': {'participant_ids': ['chulan', 'baobao', 'wangye']},
            })
            assert response.status_code == 201, response.text
            mid = response.json()['mission']['id']
            deadline = time.monotonic() + args.timeout_seconds
            if adapter:
                assert adapter.ready.wait(10), 'Second speaker did not start'
            else:
                while time.monotonic() < deadline:
                    detail = client.get(f'/api/missions/{mid}').json()
                    if any(m['role'] == 'agent' for m in detail['messages']):
                        break
                    if detail['mission']['status'] in ('blocked', 'failed', 'completed'):
                        raise AssertionError(detail)
                    time.sleep(0.2)
            try:
                response = client.post(f'/api/missions/{mid}/interruptions', json={
                    'content': '只有一周开发时间，请据此缩小范围。', 'target_agent_id': 'wangye', 'request_id': 'smoke-input',
                })
                assert response.status_code == 202, response.text
            finally:
                if adapter:
                    adapter.release.set()
            while time.monotonic() < deadline:
                detail = client.get(f'/api/missions/{mid}').json()
                if detail['discussion']['phase'] == 'ended':
                    break
                time.sleep(0.05)
            assert detail['mission']['status'] == 'completed', detail
            assert detail['discussion']['turns_used'] == 7, detail['discussion']
            assert detail['discussion']['summary_message_ids']
            assert len(detail['interrupts']) == 1 and detail['interrupts'][0]['status'] == 'handled'
            assert sum(m['content'] == '只有一周开发时间，请据此缩小范围。' for m in detail['messages']) == 1
            assert not detail['artifacts'], detail['artifacts']
            stream = client.get(f'/api/missions/{mid}/stream').text
            final = json.loads(next(line[6:] for line in stream.splitlines() if line.startswith('data: ')))
            assert final['discussion'] == detail['discussion']
            print(json.dumps({'mode': 'real' if args.real else 'fake', 'status': detail['mission']['status'],
                              'discussion': detail['discussion'], 'usage': detail.get('usage_summary')}, ensure_ascii=False, indent=2))
            print('PASS: 观点 → 回应 → API 插话 → 修正 → 总结；SSE 与终态一致')
            if args.real:
                print('真实发言内容仍需人工阅读评估；自动检查不判定人格或讨论质量。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
