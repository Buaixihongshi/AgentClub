import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.domain.models import Conversation, ConversationStatus, MissionStatus
from backend.app.storage.repository import Repository
from backend.app.work.adaptive import AdaptiveWork
from backend.app.work.service import SharedWork, WorkError
from backend.app.work.snapshots import content, snapshot


@pytest.fixture
def adaptive(repository, tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'docs').mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '--allow-empty', '-qm', 'initial'], check=True)
    repository.create_conversation(Conversation(id='c_work', title='自主工作'))
    return AdaptiveWork(repository, root, check_timeout=1)


def call(work, action, data=None, *, actor='human', mission=None, operation_id=None, revision=None):
    return work.command('c_work', action, data or {}, work.read('c_work')['revision'] if revision is None else revision,
        actor=actor, mission_id=mission.id if mission else None, turn_id='turn_test' if mission else None,
        operation_id=operation_id or uuid4().hex)


def create(work, *, risks=None, delivery='development', commands=None):
    return call(work, 'create_work', {'title': '完成需求', 'owner_id': 'chulan', 'agreement': {
        'source': '用户要求交付一个真实结果', 'goal': '交付可验证结果', 'scope': '本项目',
        'criteria': [{'id': 'AC-1', 'text': '交付文件包含真实结果'}], 'risks': risks or ['local'],
        'delivery': delivery, 'validation_commands': commands or []}})['tasks'][-1]['id']


def run(work):
    mission = work.claim('c_work')
    assert mission is not None
    work.repo.update_mission(mission.model_copy(update={'status': MissionStatus.RUNNING}))
    work.authorize_provider(mission.id, 'test')
    return mission


def finish(work, mission, success=True):
    work.repo.update_mission(mission.model_copy(update={'status': MissionStatus.COMPLETED if success else MissionStatus.BLOCKED}))
    work.finish('c_work', mission.id)
    return work.read('c_work')


def submit(work, mission, task_id, path='docs/result.md', text='真实结果', *, kind='code', reviewer_id=None):
    (work.root / path).write_text(text)
    criteria = next(t['criterion_ids'] for t in work.read('c_work')['tasks'] if t['id'] == task_id)
    return call(work, 'submit', {'task_id': task_id, 'kind': kind, 'reviewer_id': reviewer_id,
        'summary': '已交付并核对结果', 'artifact_paths': [path],
        'observations': [{'id': key, 'text': '读取文件，内容为真实结果'} for key in criteria]}, actor=mission.target_agent_id, mission=mission)


def test_single_agent_minimal_delivery_persists_dirty_content(adaptive):
    task_id = create(adaptive)
    mission = run(adaptive)
    submit(adaptive, mission, task_id)
    board = finish(adaptive, mission)
    task = board['tasks'][0]
    assert task['status'] == 'done'
    assert task['reviews'] == []
    assert task['attempts_used'] == 1
    assert task['evidence'][0]['passed']
    assert content(adaptive.repo, 'c_work', task['candidate']['id'], 'docs/result.md') == '真实结果'.encode()
    restored = SharedWork(Repository(adaptive.repo.database_path), adaptive.root).read('c_work')
    assert restored['tasks'][0]['deliveries'] == task['deliveries']


def test_submission_without_kind_cannot_silently_make_designer_a_code_author(adaptive):
    from pydantic import ValidationError
    task_id = create(adaptive)
    mission = run(adaptive)
    (adaptive.root / 'docs/design.html').write_text('<html>设计候选</html>')
    with pytest.raises(ValidationError, match='kind'):
        call(adaptive, 'submit', {'task_id': task_id, 'summary': '设计已完成',
            'artifact_paths': ['docs/design.html'], 'observations': [{'id': 'AC-1', 'text': '已核对设计'}]},
            actor='chulan', mission=mission)
    task = adaptive.read('c_work')['tasks'][0]
    assert task['authors'] == []
    assert task.get('submission') is None


def test_design_author_can_review_another_agents_implementation(adaptive):
    task_id = create(adaptive)
    planner = run(adaptive)
    design_id = adaptive.delegate('c_work', planner.id, 'chulan', 'turn_test', 'wangye',
        '交付设计候选', 'wait_reply', 'delegate-design')['task_id']
    finish(adaptive, planner)
    designer = run(adaptive)
    submit(adaptive, designer, design_id, path='docs/design.html', kind='design')
    finish(adaptive, designer)
    planner = run(adaptive)
    code_id = adaptive.delegate('c_work', planner.id, 'chulan', 'turn_test', 'baobao',
        '实现选定方案', 'wait_reply', 'delegate-code')['task_id']
    finish(adaptive, planner)
    developer = run(adaptive)
    submit(adaptive, developer, code_id, path='docs/implementation.html', kind='code')
    finish(adaptive, developer)
    planner = run(adaptive)
    submit(adaptive, planner, task_id, reviewer_id='wangye')
    finish(adaptive, planner)
    reviewer = run(adaptive)
    assert reviewer.target_agent_id == 'wangye'
    task = adaptive.read('c_work')['tasks'][0]
    assert set(task['authors']) == {'chulan', 'baobao'}
    call(adaptive, 'review', {'task_id': task_id, 'candidate_id': task['candidate']['id'],
        'criterion_ids': task['criterion_ids'], 'decision': 'approved', 'note': '对照约定核对他人实现'},
        actor='wangye', mission=reviewer)
    assert finish(adaptive, reviewer)['tasks'][0]['status'] == 'done'


def test_strict_contract_and_idempotent_human_commands(adaptive):
    task_id = create(adaptive)
    revision = adaptive.read('c_work')['revision']
    first = call(adaptive, 'pause', {'task_id': task_id}, operation_id='pause-one', revision=revision)
    assert call(adaptive, 'pause', {'task_id': task_id}, operation_id='pause-one', revision=revision) == first
    with pytest.raises(WorkError, match='operation_id'):
        call(adaptive, 'resume', {'task_id': task_id}, operation_id='pause-one')
    with pytest.raises(WorkError, match='已更新'):
        call(adaptive, 'resume', {'task_id': task_id}, revision=0)


def test_atomic_claim_and_pause_continue(adaptive):
    task_id = create(adaptive)
    with ThreadPoolExecutor(max_workers=4) as pool:
        missions = list(pool.map(lambda _: AdaptiveWork(Repository(adaptive.repo.database_path), adaptive.root).claim('c_work'), range(4)))
    assert sum(m is not None for m in missions) == 1
    mission = next(m for m in missions if m)
    adaptive.repo.update_mission(mission.model_copy(update={'status': MissionStatus.RUNNING}))
    call(adaptive, 'pause', {'task_id': task_id})
    call(adaptive, 'continue_work', {'task_id': task_id, 'text': '调查结束', 'next_action': '落实修复'}, actor='chulan', mission=mission)
    finish(adaptive, mission)
    assert adaptive.claim('c_work') is None
    call(adaptive, 'resume', {'task_id': task_id})
    assert adaptive.claim('c_work') is not None


def test_high_risk_review_and_stale_version_cannot_approve(adaptive):
    task_id = create(adaptive, risks=['security'])
    mission = run(adaptive)
    submit(adaptive, mission, task_id)
    board = finish(adaptive, mission)
    assert board['tasks'][0]['status'] == 'ready'
    reviewer = run(adaptive)
    assert reviewer.target_agent_id != 'chulan'
    task = adaptive.read('c_work')['tasks'][0]
    data = {'task_id': task_id, 'candidate_id': task['candidate']['id'], 'criterion_ids': ['AC-1'],
            'decision': 'approved', 'note': '对照约定和真实文件逐项验证'}
    (adaptive.root / 'docs/result.md').write_text('变更')
    with pytest.raises(WorkError, match='变化'):
        call(adaptive, 'review', data, actor=reviewer.target_agent_id, mission=reviewer)
    (adaptive.root / 'docs/result.md').write_text('真实结果')
    call(adaptive, 'review', data, actor=reviewer.target_agent_id, mission=reviewer)
    assert finish(adaptive, reviewer)['tasks'][0]['status'] == 'done'


def test_author_cannot_self_review_or_change_identity(adaptive):
    task_id = create(adaptive)
    mission = run(adaptive)
    with pytest.raises(WorkError, match='责任人'):
        call(adaptive, 'continue_work', {'task_id': task_id, 'text': '冒领', 'next_action': '错误'}, actor='wangye', mission=mission)
    with pytest.raises(WorkError, match='非作者'):
        call(adaptive, 'review', {'task_id': task_id}, actor='chulan', mission=mission)
    finish(adaptive, mission)
    with pytest.raises(WorkError, match='过期'):
        call(adaptive, 'block', {'task_id': task_id, 'reason': '旧输出'}, actor='chulan', mission=mission)


def test_design_decision_version_and_duplicate_acceptance(adaptive):
    task_id = create(adaptive)
    mission = run(adaptive)
    (adaptive.root / 'docs/ui.md').write_text('A / B 两版界面')
    call(adaptive, 'request_decision', {'task_id': task_id, 'kind': 'design', 'question': '选择哪版？',
        'options': [{'id': 'A', 'text': '方案 A'}, {'id': 'B', 'text': '方案 B'}], 'artifact_paths': ['docs/ui.md']},
        actor='chulan', mission=mission)
    board = finish(adaptive, mission)
    decision = board['decisions'][0]
    assert adaptive.claim('c_work') is None
    data = {'decision_id': decision['id'], 'option_id': 'B', 'note': '按钮放底部'}
    board = call(adaptive, 'decide', data, operation_id='choose-b')
    assert board['decisions'][0]['option_id'] == 'B'
    assert call(adaptive, 'decide', data, operation_id='choose-b') == board
    assert run(adaptive).target_agent_id == 'chulan'


def test_changed_design_does_not_consume_old_approval(adaptive):
    task_id = create(adaptive)
    mission = run(adaptive)
    (adaptive.root / 'docs/ui.md').write_text('原版')
    call(adaptive, 'request_decision', {'task_id': task_id, 'question': '选 A？', 'options': [{'id': 'A', 'text': 'A'}],
         'artifact_paths': ['docs/ui.md']}, actor='chulan', mission=mission)
    board = finish(adaptive, mission)
    (adaptive.root / 'docs/ui.md').write_text('新版')
    board = call(adaptive, 'decide', {'decision_id': board['decisions'][0]['id'], 'option_id': 'A'})
    assert board['decisions'][0]['status'] == 'stale'
    assert board['tasks'][0]['status'] == 'ready'


def test_delegation_keeps_parent_accountable_and_integration_required(adaptive):
    task_id = create(adaptive)
    mission = run(adaptive)
    result = adaptive.delegate('c_work', mission.id, 'chulan', 'turn_test', 'baobao', '产出功能文件', 'wait_reply', 'handoff-1')
    assert adaptive.delegate('c_work', mission.id, 'chulan', 'turn_test', 'baobao', '产出功能文件', 'wait_reply', 'handoff-1') == result
    finish(adaptive, mission)
    child = run(adaptive)
    assert child.target_agent_id == 'baobao'
    with pytest.raises(WorkError, match='循环'):
        adaptive.delegate('c_work', child.id, 'baobao', 'turn_test', 'chulan', '交回', 'wait_reply', 'cycle')
    submit(adaptive, child, result['task_id'])
    board = finish(adaptive, child)
    assert board['tasks'][1]['status'] == 'done'
    assert board['tasks'][0]['status'] == 'ready'
    assert board['tasks'][0]['owner_id'] == 'chulan'
    assert run(adaptive).target_agent_id == 'chulan'


def test_original_scope_cannot_be_expanded_by_agent(adaptive):
    task_id = create(adaptive, delivery='document')
    mission = run(adaptive)
    with pytest.raises(WorkError, match='目标、范围或授权'):
        call(adaptive, 'refine_agreement', {'task_id': task_id, 'agreement': {'delivery': 'development'}}, actor='chulan', mission=mission)
    with pytest.raises(WorkError, match='权限'):
        call(adaptive, 'create_work', {}, actor='chulan', mission=mission)
    board = call(adaptive, 'refine_agreement', {'task_id': task_id, 'agreement': {
        'criteria': [{'id': 'AC-SPEC', 'text': 'Spec 含验收标准'}]}}, actor='chulan', mission=mission)
    assert board['contracts'][-1]['delivery'] == 'document'
    assert board['tasks'][0]['criterion_ids'] == ['AC-SPEC']


def test_no_structured_result_and_root_budget_do_not_fake_completion(adaptive):
    task_id = create(adaptive)
    mission = run(adaptive)
    board = finish(adaptive, mission)
    assert board['tasks'][0]['status'] == 'waiting'
    assert not board['tasks'][0]['evidence']
    call(adaptive, 'retry', {'task_id': task_id})
    with adaptive.repo._transaction(immediate=True) as db:
        board = adaptive._load(db, 'c_work')
        board['tasks'][0]['attempts_used'] = board['tasks'][0]['max_attempts']
        adaptive._save(db, board, 'fixture', 'host')
    assert adaptive.claim('c_work') is None
    assert adaptive.read('c_work')['tasks'][0]['waiting_reason'] == 'budget'
    call(adaptive, 'adjust_budget', {'task_id': task_id, 'max_attempts': 24})
    assert adaptive.claim('c_work') is not None


def test_online_goal_waits_for_exact_user_approval_and_configuration(adaptive):
    task_id = create(adaptive, delivery='online')
    mission = run(adaptive)
    submit(adaptive, mission, task_id)
    board = finish(adaptive, mission)
    assert board['tasks'][0]['status'] == 'waiting'
    decision = board['decisions'][0]
    assert decision['kind'] == 'release'
    assert not board['tasks'][0].get('release')
    board = call(adaptive, 'decide', {'decision_id': decision['id'], 'option_id': 'approve'})
    assert board['tasks'][0]['waiting_reason'] == 'release_configuration'
    assert board['tasks'][0]['release']['status'] == 'awaiting_adapter'


def test_revise_keeps_historical_evidence_and_does_not_reuse_done(adaptive):
    task_id = create(adaptive)
    mission = run(adaptive)
    submit(adaptive, mission, task_id)
    original = finish(adaptive, mission)
    agreement = {k: original['contracts'][0][k] for k in ['source', 'goal', 'scope', 'criteria', 'delivery']}
    agreement['criteria'] = [{'id': 'AC-2', 'text': '新验收条件'}]
    revised = call(adaptive, 'revise_work', {'task_id': task_id, 'agreement': agreement})
    task = revised['tasks'][0]
    assert task['status'] == 'ready'
    assert task['candidate'] is None
    assert task['evidence'] == original['tasks'][0]['evidence']
    assert task['deliveries'] == original['tasks'][0]['deliveries']
    assert task['attempts_used'] == 1


def test_snapshot_outside_workspace_rejected_and_uncommitted_changes_versioned(adaptive):
    (adaptive.root / 'docs/a.md').write_text('一')
    first = snapshot(adaptive.root)
    (adaptive.root / 'docs/a.md').write_text('二')
    assert snapshot(adaptive.root)['id'] != first['id']
    with pytest.raises(WorkError, match='超出'):
        snapshot(adaptive.root, ['../secret'])


def test_restart_safely_resumes_unchanged_and_preserves_user_pause(adaptive):
    task_id = create(adaptive)
    mission = run(adaptive)
    call(adaptive, 'pause', {'task_id': task_id})
    with adaptive.repo._transaction(immediate=True) as db:
        board = adaptive._load(db, 'c_work')
        board['tasks'][0]['attempts'][-1]['host_pid'] = 99999999
        adaptive._save(db, board, 'fixture', 'host')
    adaptive.recover()
    assert adaptive.read('c_work')['tasks'][0]['waiting_reason'] == 'paused'
    assert adaptive.claim('c_work') is None
    call(adaptive, 'resume', {'task_id': task_id})
    resumed = adaptive.claim('c_work')
    assert resumed and resumed.id != mission.id
    with pytest.raises(WorkError, match='过期'):
        call(adaptive, 'block', {'task_id': task_id, 'reason': '旧进程'}, actor='chulan', mission=mission)


def test_real_verification_failure_is_preserved_then_repaired(adaptive):
    from backend.app.adapters.deliberation_cli import isolation_supported
    if not isolation_supported():
        pytest.skip('real constrained validation requires macOS sandbox-exec')
    task_id = create(adaptive, commands=[[sys.executable, '-c', 'print("observable failure"); raise SystemExit(7)']])
    mission = run(adaptive)
    submit(adaptive, mission, task_id)
    task = finish(adaptive, mission)['tasks'][0]
    assert task['status'] == 'ready'
    check = task['evidence'][0]['checks'][0]
    assert check['exit_code'] == 7
    assert 'observable failure' in adaptive.read_log('c_work', check['log_id']).decode()
    assert not task['evidence'][0]['passed']


def test_scope_is_enforced_by_os_sandbox(adaptive, tmp_path):
    from backend.app.adapters.deliberation_cli import isolation_supported
    from backend.app.work.runtime import run_check
    if not isolation_supported():
        pytest.skip('macOS sandbox is unavailable')
    outside = tmp_path / 'outside.txt'
    raw, code = run_check(adaptive.root, [sys.executable, '-c',
        f'from pathlib import Path; Path({str(outside)!r}).write_text("not authorized")'], 5)
    assert code != 0 and not outside.exists()
    raw, code = run_check(adaptive.root, [sys.executable, '-c',
        'from pathlib import Path; Path("docs/spec.md").write_text("spec")'], 5, delivery='document')
    assert code == 0, raw
    raw, code = run_check(adaptive.root, [sys.executable, '-c',
        'from pathlib import Path; Path("source.py").write_text("unauthorized")'], 5, delivery='document')
    assert code != 0 and not (adaptive.root / 'source.py').exists()


def test_live_orphan_process_prevents_recovery_writes(adaptive):
    task_id = create(adaptive)
    mission = run(adaptive)
    with adaptive.repo._transaction(immediate=True) as db:
        board = adaptive._load(db, 'c_work')
        board['tasks'][0]['attempts'][-1].update(host_pid=99999999, cli_pid=os.getpid())
        adaptive._save(db, board, 'fixture', 'host')
    adaptive.recover()
    task = adaptive.read('c_work')['tasks'][0]
    assert task['status'] == 'waiting' and task['old_process_alive']
    assert adaptive.claim('c_work') is None
    with pytest.raises(WorkError, match='旧进程'):
        call(adaptive, 'retry', {'task_id': task_id})


def test_changed_workspace_recovers_as_readonly_reconciliation(adaptive):
    create(adaptive)
    run(adaptive)
    (adaptive.root / 'docs/partial.md').write_text('已经写入，不得重放')
    with adaptive.repo._transaction(immediate=True) as db:
        board = adaptive._load(db, 'c_work')
        board['tasks'][0]['attempts'][-1]['host_pid'] = 99999999
        adaptive._save(db, board, 'fixture', 'host')
    adaptive.recover()
    task = adaptive.read('c_work')['tasks'][0]
    assert task['status'] == 'ready' and task['next_kind'] == 'reconcile'
    resumed = run(adaptive)
    assert adaptive.execution_context(resumed.id)['task']['attempts'][-1]['stage'] == 'reconcile'


def test_configured_release_executes_only_approved_candidate_and_queries_actual_state(adaptive):
    from backend.app.work.releases import process
    (adaptive.root / '.gitignore').write_text('.agenthub/\n')
    config = adaptive.root / '.agenthub/work.local.json'
    config.parent.mkdir()
    # Deployment counter lives in ignored adapter storage, not candidate code.
    counter = config.parent / 'calls'
    config.write_text(json.dumps({'release': {'environment': 'staging', 'command': [sys.executable, '-c',
        f'from pathlib import Path; p=Path({str(counter)!r}); p.write_text(p.read_text()+"x" if p.exists() else "x")'],
        'verify_command': [sys.executable, '-c', 'import os,json; print(json.dumps({"candidate_id":os.environ["AGENTHUB_CANDIDATE_ID"],"environment":os.environ["AGENTHUB_TARGET_ENVIRONMENT"],"status":"published"}))']}}))
    task_id = create(adaptive, delivery='online')
    mission = run(adaptive)
    submit(adaptive, mission, task_id)
    board = finish(adaptive, mission)
    assert not process(adaptive, 'c_work')
    assert not counter.exists()
    decision = board['decisions'][0]
    assert decision['environment'] == 'staging'
    call(adaptive, 'decide', {'decision_id': decision['id'], 'option_id': 'approve'})
    assert process(adaptive, 'c_work')
    assert counter.read_text() == 'x'
    assert adaptive.read('c_work')['tasks'][0]['status'] == 'done'
    assert not process(adaptive, 'c_work')
    assert counter.read_text() == 'x'


def test_unknown_release_queries_once_without_replaying_deployment(adaptive):
    from backend.app.work.releases import process
    (adaptive.root / '.gitignore').write_text('.agenthub/\n')
    config = adaptive.root / '.agenthub/work.local.json'
    config.parent.mkdir()
    counter = config.parent / 'calls'
    config.write_text(json.dumps({'release': {'environment': 'production', 'command': [sys.executable, '-c',
        f'from pathlib import Path; Path({str(counter)!r}).write_text("deployed")'],
        'verify_command': [sys.executable, '-c', 'print("not verified")']}}))
    task_id = create(adaptive, delivery='online')
    mission = run(adaptive)
    submit(adaptive, mission, task_id)
    board = finish(adaptive, mission)
    call(adaptive, 'decide', {'decision_id': board['decisions'][0]['id'], 'option_id': 'approve'})
    process(adaptive, 'c_work')
    assert adaptive.read('c_work')['tasks'][0]['release']['status'] == 'unconfirmed'
    counter.unlink()
    call(adaptive, 'retry', {'task_id': task_id})
    process(adaptive, 'c_work')
    assert not counter.exists()


def test_runner_mcp_delegation_and_integration_flow(adaptive, temp_settings, monkeypatch):
    from backend.app.adapters.base import CLIAdapter, TurnOutcome
    from backend.app.domain.models import TurnStatus
    from backend.app.api.shared_work import drain
    config_dir = Path('config').resolve()
    monkeypatch.chdir(adaptive.root)
    task_id = create(adaptive)
    settings = temp_settings.model_copy(update={'config_dir': config_dir})
    class Worker(CLIAdapter):
        def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
            view = tools.shared_work(mission_id, agent_id, turn_id, provider_id)
            task = next(t for t in view['tasks'] if t['active_mission_id'] == mission_id)
            if not task['parent_id'] and not task['depends_on']:
                tools.invoke_agent(mission_id, agent_id, turn_id, 'baobao', '交付可核对的实现文件', provider_id, mode='wait_reply')
            else:
                (adaptive.root / 'docs/result.md').write_text('真实结果')
                tools.shared_work(mission_id, agent_id, turn_id, provider_id, 'submit', {
                    'task_id': task['id'], 'kind': 'code', 'summary': '已集成并验收' if not task['parent_id'] else '实现完成',
                    'artifact_paths': ['docs/result.md'], 'observations': [{'id': key, 'text': '实际读取并核对文件内容'} for key in task['criterion_ids']]},
                    view['revision'], operation_id='deliver')
            tools.publish_message(mission_id, agent_id, turn_id, '进展已保存', provider_id)
            return TurnOutcome(status=TurnStatus.COMPLETED)
    drain(adaptive.repo, settings, 'c_work', Worker())
    board = adaptive.read('c_work')
    assert [t['status'] for t in board['tasks']] == ['done', 'done']
    assert [m.target_agent_id for m in adaptive.repo.list_missions_for_conversation('c_work')] == ['chulan', 'baobao', 'chulan']
    assert board['tasks'][0]['attempts_used'] == 3


def test_api_intake_document_and_snapshot_scope(adaptive, temp_settings, monkeypatch):
    from backend.app.adapters.base import CLIAdapter, TurnOutcome
    from backend.app.domain.models import TurnStatus
    from backend.app.main import create_app
    from fastapi.testclient import TestClient
    import time
    config_dir = Path('config').resolve()
    monkeypatch.chdir(adaptive.root)
    class DocumentWorker(CLIAdapter):
        def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
            view = tools.shared_work(mission_id, agent_id, turn_id, provider_id)
            task = next(t for t in view['tasks'] if t['active_mission_id'] == mission_id)
            assert view['contracts'][0]['delivery'] == 'document'
            (adaptive.root / 'docs/spec.md').write_text('Spec 与验收标准')
            tools.shared_work(mission_id, agent_id, turn_id, provider_id, 'submit', {'task_id': task['id'],
                'kind': 'document', 'summary': 'Spec 已完成', 'artifact_paths': ['docs/spec.md'], 'observations': [{'id': 'AC-1', 'text': '文档含验收标准'}]},
                view['revision'], operation_id='deliver')
            tools.publish_message(mission_id, agent_id, turn_id, '已交付 Spec', provider_id)
            return TurnOutcome(status=TurnStatus.COMPLETED)
    settings = temp_settings.model_copy(update={'config_dir': config_dir})
    client = TestClient(create_app(settings, mission_adapter=DocumentWorker()))
    response = client.post('/api/conversations/c_work/missions', json={'goal': '先写一份 spec', 'target_agent_ids': ['chulan'],
        'work_mode': 'document', 'request_id': 'intake-one'})
    assert response.status_code == 201, response.text
    for _ in range(200):
        board = client.get('/api/conversations/c_work/work').json()
        if board['tasks'][0]['status'] == 'done':
            break
        time.sleep(.01)
    assert board['tasks'][0]['status'] == 'done'
    assert len(adaptive.repo.list_missions()) == 1
    version = board['tasks'][0]['candidate']['id']
    response = client.get(f'/api/conversations/c_work/work/snapshots/{version}/file', params={'path': 'docs/spec.md'})
    assert response.text == 'Spec 与验收标准'
    assert 'sandbox' in response.headers['content-security-policy']
    assert client.get(f'/api/conversations/missing/work/snapshots/{version}/file', params={'path': 'docs/spec.md'}).status_code == 404
    assert client.get('/api/conversations/c_work').json()['has_autonomous_work']


def test_transfer_waits_for_target_acceptance_without_human_handoff(adaptive):
    task_id = create(adaptive)
    original = run(adaptive)
    call(adaptive, 'propose_transfer', {'task_id': task_id, 'owner_id': 'wangye', 'reason': '后续以视觉体验交付为主'},
         actor='chulan', mission=original)
    assert finish(adaptive, original)['tasks'][0]['owner_id'] == 'chulan'
    target = run(adaptive)
    assert target.target_agent_id == 'wangye'
    assert adaptive.execution_context(target.id)['task']['attempts'][-1]['stage'] == 'handoff'
    board = call(adaptive, 'accept_transfer', {'task_id': task_id}, actor='wangye', mission=target)
    assert board['tasks'][0]['owner_id'] == 'wangye'
    assert board['tasks'][0]['transfer']['accepted_by'] == 'wangye'
    assert finish(adaptive, target)['tasks'][0]['next_kind'] == 'work'


def test_host_risk_floor_detects_auth_changes_even_if_author_declares_local(adaptive):
    task_id = create(adaptive)
    mission = run(adaptive)
    (adaptive.root / 'backend/app').mkdir(parents=True)
    submit(adaptive, mission, task_id, path='backend/app/auth.py', text='def allowed(): return True\n')
    task = finish(adaptive, mission)['tasks'][0]
    assert 'security' in task['evidence'][0]['detected_risks']
    assert task['review_required'] and task['status'] == 'ready'
    assert run(adaptive).target_agent_id != 'chulan'


def test_revision_invalidates_pending_child_decisions_and_preserves_old_records(adaptive):
    task_id = create(adaptive)
    parent = run(adaptive)
    child_id = adaptive.delegate('c_work', parent.id, 'chulan', 'turn_test', 'wangye', '设计原型', 'wait_reply', 'design')['task_id']
    finish(adaptive, parent)
    designer = run(adaptive)
    call(adaptive, 'request_decision', {'task_id': child_id, 'question': '选择 A？', 'options': [{'id': 'A', 'text': 'A'}]},
         actor='wangye', mission=designer)
    before = finish(adaptive, designer)
    agreement = {k: before['contracts'][0][k] for k in ['source', 'goal', 'scope', 'criteria', 'delivery']}
    agreement['goal'] = '改成无 UI 的任务'
    board = call(adaptive, 'revise_work', {'task_id': task_id, 'agreement': agreement})
    assert board['decisions'][0]['status'] == 'stale'
    assert board['tasks'][1]['status'] == 'cancelled'
    assert board['tasks'][0]['depends_on'] == []


def test_release_rejection_allows_new_candidate_and_new_approval(adaptive):
    task_id = create(adaptive, delivery='online')
    mission = run(adaptive)
    submit(adaptive, mission, task_id)
    board = finish(adaptive, mission)
    call(adaptive, 'decide', {'decision_id': board['decisions'][0]['id'], 'option_id': 'revise', 'note': '补充说明'})
    again = run(adaptive)
    submit(adaptive, again, task_id, text='真实结果，已补充说明')
    board = finish(adaptive, again)
    assert len(board['decisions']) == 2
    assert board['decisions'][0]['status'] == 'stale'
    assert board['decisions'][1]['status'] == 'pending'
    assert board['decisions'][0]['candidate_id'] != board['decisions'][1]['candidate_id']


def test_unambiguous_chat_answer_is_recorded_without_model_approval(adaptive, temp_settings, monkeypatch):
    from backend.app.main import create_app
    from fastapi.testclient import TestClient
    config_dir = Path('config').resolve()
    task_id = create(adaptive)
    mission = run(adaptive)
    call(adaptive, 'request_decision', {'task_id': task_id, 'question': '选择哪版？',
         'options': [{'id': 'A', 'text': 'A 方案'}, {'id': 'B', 'text': 'B 方案'}]}, actor='chulan', mission=mission)
    finish(adaptive, mission)
    monkeypatch.chdir(adaptive.root)
    client = TestClient(create_app(temp_settings.model_copy(update={'config_dir': config_dir})))
    response = client.post('/api/conversations/c_work/missions', json={'goal': '选 B，按钮放底部', 'request_id': 'human-answer'})
    assert response.status_code == 201, response.text
    board = adaptive.read('c_work')
    assert board['decisions'][0]['option_id'] == 'B'
    assert board['decisions'][0]['note'] == '按钮放底部'
    assert len(board['tasks'][0]['attempts']) == 1
    replay = client.post('/api/conversations/c_work/missions', json={'goal': '选 B，按钮放底部', 'request_id': 'human-answer'})
    assert replay.status_code == 201
    assert len(adaptive.repo.list_missions()) == 2


def test_ambiguous_chat_or_multiple_pending_decisions_never_approve():
    from backend.app.work.decisions import match_answer
    decision = {'id': 'd', 'status': 'pending', 'kind': 'release', 'options': [{'id': 'approve', 'text': '批准此版本上线'}]}
    assert match_answer({'decisions': [decision]}, '可以吧') is None
    assert match_answer({'decisions': [decision, {**decision, 'id': 'd2'}]}, '批准上线') is None
    assert match_answer({'decisions': [decision]}, '批准上线')['option_id'] == 'approve'


def test_review_has_no_host_write_or_lifecycle_escape(adaptive, tmp_path):
    from backend.app.mcp.tools import MCPTools, MCPToolError
    task_id = create(adaptive, risks=['security'])
    mission = run(adaptive)
    submit(adaptive, mission, task_id)
    finish(adaptive, mission)
    reviewer = run(adaptive)
    tools = MCPTools(adaptive.repo, tmp_path / 'artifacts', workspace_root=adaptive.root)
    with pytest.raises(MCPToolError, match='只读'):
        tools.write_artifact(reviewer.id, reviewer.target_agent_id, 'turn_test', 'escape.md', 'write')
    with pytest.raises(MCPToolError, match='宿主'):
        tools.update_status(reviewer.id, 'completed', agent_id=reviewer.target_agent_id)


def conversation_proposal(work, *, actor='chulan', delivery='document', mission_id='m_intake', operation_id='proposal'):
    from backend.app.domain.models import Mission
    mission = Mission(id=mission_id, conversation_id='c_work', title='页面方案', goal='@张楚岚 帮我设计小程序页面，先给一版方案', status=MissionStatus.RUNNING)
    if not work.repo.get_mission(mission.id):
        work.repo.create_mission(mission)
        work.repo.add_event(mission.id, 'turn.started', {'turn_id': 'turn_test', 'agent_id': actor})
    return call(work, 'propose_work', {'title': '小程序页面方案', 'owner_id': actor, 'agreement': {
        'source': '模型填写的来源不算人类授权', 'goal': '交付页面方案', 'scope': '页面信息结构与交互', 'delivery': delivery,
        'criteria': [{'id': 'AC-1', 'text': '呈现结构与交互方案'}]}}, actor=actor, mission=mission, operation_id=operation_id), mission


def test_conversation_proposal_is_durable_but_never_scheduled_before_human_accepts(adaptive):
    board, mission = conversation_proposal(adaptive)
    proposal = board['proposals'][0]
    assert board['tasks'] == [] and board['contracts'] == []
    assert proposal['agreement']['source'] == mission.goal
    assert proposal['agreement']['risks'] == ['local']
    assert adaptive.claim('c_work') is None
    adaptive.repo.update_mission(mission.model_copy(update={'status': MissionStatus.COMPLETED}))
    assert adaptive.claim('c_work') is None
    reloaded = AdaptiveWork(adaptive.repo, adaptive.root)
    assert reloaded.read('c_work')['proposals'][0]['digest'] == proposal['digest']
    accepted = call(reloaded, 'accept_proposal', {'proposal_id': proposal['id']}, operation_id='human-confirm')
    task = accepted['tasks'][0]
    assert task['authorization']['actor'] == 'human'
    assert task['authorization']['proposal_id'] == proposal['id']
    assert task['authorization']['proposal_digest'] == proposal['digest']
    assert task['authorization']['capabilities'] == ['read', 'document']
    assert task['authorization']['publish'] is False
    again = call(reloaded, 'accept_proposal', {'proposal_id': proposal['id']}, operation_id='human-confirm', revision=board['revision'])
    assert again['tasks'][0]['id'] == task['id']
    assert len(reloaded.read('c_work')['tasks']) == 1
    assert reloaded.claim('c_work').target_agent_id == 'chulan'


def test_proposal_needs_live_bound_role_and_cannot_be_accepted_by_a_model(adaptive):
    board, mission = conversation_proposal(adaptive)
    proposal_id = board['proposals'][0]['id']
    with pytest.raises(WorkError, match='权限'):
        call(adaptive, 'accept_proposal', {'proposal_id': proposal_id}, actor='chulan', mission=mission)
    adaptive.repo.add_event(mission.id, 'turn.completed', {'turn_id': 'turn_test', 'agent_id': 'chulan'})
    with pytest.raises(WorkError, match='正在回应'):
        conversation_proposal(adaptive, operation_id='late-proposal')
    assert adaptive.read('c_work')['tasks'] == []


def test_new_human_context_supersedes_proposal_and_stale_revision_cannot_start(adaptive):
    from backend.app.domain.models import Message, MessageRole
    board, mission = conversation_proposal(adaptive)
    proposal_id = board['proposals'][0]['id']
    with pytest.raises(WorkError, match='任务板已更新'):
        call(adaptive, 'accept_proposal', {'proposal_id': proposal_id}, revision=0)
    adaptive.repo.add_message(Message(id='msg_new_requirement', mission_id=mission.id, conversation_id='c_work', role=MessageRole.USER, content='改成只做预约页'))
    assert adaptive.read('c_work')['proposals'][0]['status'] == 'superseded'
    with pytest.raises(WorkError, match='已被新版本替代'):
        call(adaptive, 'accept_proposal', {'proposal_id': proposal_id})
    assert adaptive.read('c_work')['tasks'] == []


def test_declined_and_archived_proposals_never_create_work(adaptive):
    board, _ = conversation_proposal(adaptive)
    declined = call(adaptive, 'reject_proposal', {'proposal_id': board['proposals'][0]['id']})
    assert declined['proposals'][0]['status'] == 'rejected'
    assert declined['tasks'] == []
    board, _ = conversation_proposal(adaptive, operation_id='revised-proposal')
    conversation = adaptive.repo.get_conversation('c_work')
    adaptive.repo.update_conversation(conversation.model_copy(update={'status': ConversationStatus.ARCHIVED}))
    with pytest.raises(WorkError, match='恢复会话'):
        call(adaptive, 'accept_proposal', {'proposal_id': board['proposals'][-1]['id']})
    assert adaptive.read('c_work')['tasks'] == []


def test_proposal_defaults_to_document_and_rejects_foreign_turn(adaptive):
    board, mission = conversation_proposal(adaptive)
    proposal = board['proposals'][0]
    payload = {key: proposal[key] for key in ('title', 'owner_id', 'agreement')}
    payload['agreement'] = dict(payload['agreement'])
    payload['agreement'].pop('delivery')
    defaulted = call(adaptive, 'propose_work', payload, actor='chulan', mission=mission)
    assert defaulted['proposals'][-1]['agreement']['delivery'] == 'document'
    with pytest.raises(WorkError, match='正在回应'):
        call(adaptive, 'propose_work', payload, actor='wangye', mission=mission)
    with pytest.raises(WorkError, match='已被新版本替代'):
        call(adaptive, 'accept_proposal', {'proposal_id': proposal['id']})
