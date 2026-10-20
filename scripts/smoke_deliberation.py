#!/usr/bin/env python3
"""Six-phase HTTP smoke with isolated temporary persistence; no real models."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.settings import Settings


def wait(client, mid, expected):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        detail = client.get(f'/api/missions/{mid}').json()
        if detail['discussion']['phase'] == expected:
            return detail
        if detail['mission']['status'] == 'blocked':
            raise AssertionError(detail['discussion'].get('error'))
        time.sleep(.05)
    raise AssertionError(f'Discussion did not reach {expected}')


def main():
    with tempfile.TemporaryDirectory(prefix='agentclub-v2-smoke-') as temp:
        settings = Settings(database_path=Path(temp) / 'test.sqlite3', artifact_root=Path(temp) / 'artifacts', use_fake_cli=True)
        with TestClient(create_app(settings)) as client:
            assert 'deliberation_v2' in client.get('/api/health').json()['discussion_protocols']
            response = client.post('/api/missions', json={
                'goal': '在相同约束下独立提出建议，按证据核验，保留未知。', 'room_mode': 'roundtable',
                'discussion': {'protocol': 'deliberation_v2', 'participant_ids': ['chulan', 'baobao', 'wangye']},
                'request_id': 'smoke-create',
            })
            assert response.status_code == 201, response.text
            mid = response.json()['mission']['id']
            first = wait(client, mid, 'awaiting_synthesizer')
            assert len(first['discussion']['results']) == 3
            assert first['mission']['status'] == 'awaiting_input'
            selected = client.post(f'/api/missions/{mid}/discussion-controls', json={
                'action': 'select_synthesizer', 'agent_id': 'wangye',
                'expected_revision': first['discussion']['revision'], 'request_id': 'smoke-select',
            })
            assert selected.status_code == 202, selected.text
            final = wait(client, mid, 'ended')
            view = final['discussion']
            assert view['turns_used'] == 6
            assert view['final_id'] == view['draft_id']
            assert {r['agent_id'] for r in view['results'] if r['kind'] == 'review'} == {'chulan', 'baobao'}
            assert not view['knowledge_candidate_id']
            assert not any(e['event_type'] == 'discussion.v2.state' for e in final['events'])
            print('PASS: independent barrier → explicit operator selection → synthesis → independent reviews → final result (6 calls).')


if __name__ == '__main__':
    main()
