"""Collect observations against the exact saved candidate, outside DB locks."""
import hashlib
import json
import re

from backend.app.extensions.tasks.runtime import run_check
from backend.app.extensions.tasks.service import WorkError, now, uid
from backend.app.extensions.tasks.snapshots import snapshot, read_bytes


def validate(service, conversation_id, task, agreement, mission_id, root=None):
    root = root or service.root  # The bound task workspace, passed by the host.
    candidate = task['candidate']
    paths = task['submission']['artifact_paths']
    if snapshot(root, paths)['id'] != candidate['id']:
        raise WorkError('提交后产物已变化，未将验证归到新版本')
    checks = []
    for command in agreement['validation_commands']:
        try:
            raw, code = run_check(root, command, service.check_timeout,
                delivery=agreement['delivery'], protected_paths=[service.repo.database_path.resolve(),
                    str(service.repo.database_path.resolve()) + '-wal', str(service.repo.database_path.resolve()) + '-shm'],
                on_spawn=lambda pid: service.record_process(mission_id, pid))
        except (OSError, WorkError) as exc:
            raw, code = str(exc).encode(), -1
        log_id = uid('wl_')
        with service.repo._transaction(immediate=True) as db:
            db.execute('INSERT INTO work_check_logs VALUES (?, ?, ?)', (log_id, conversation_id, raw))
        checks.append({'command': command, 'exit_code': code, 'output': raw[-24000:].decode(errors='replace'),
                       'output_sha256': hashlib.sha256(raw).hexdigest(), 'truncated': len(raw) > 24000,
                       'log_id': log_id})
    unchanged = snapshot(root, paths)['id'] == candidate['id']
    before = json.loads(read_bytes(service.repo, conversation_id, task['attempts'][-1]['before_id']))['files']
    after = json.loads(read_bytes(service.repo, conversation_id, candidate['id']))['files']
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    # Conservative project boundaries are a host-enforced floor. This does not
    # pretend to classify every semantic risk in arbitrary source code.
    patterns = {'security': r'(^|/)(auth|security|permissions?)([./_]|$)',
                'migration': r'(^|/)migrations?(/|[._])',
                'data': r'^backend/app/storage/',
                'contract': r'^backend/app/api/',
                'governance': r'^backend/app/(work|orchestration|mcp)/'}
    risks = [risk for risk, pattern in patterns.items() if any(re.search(pattern, p) for p in changed)]
    return {'id': uid('wv_'), 'contract_id': task['contract_id'], 'contract_digest': agreement['digest'],
            'candidate_id': candidate['id'], 'commit_sha': candidate['base_sha'], 'criterion_ids': task['criterion_ids'],
            'mission_id': mission_id, 'agent_id': task['owner_id'], 'created_at': now(), 'checks': checks,
            'observations': task['submission']['observations'], 'artifact_paths': paths,
            'detected_risks': risks,
            'method': 'commands_and_saved_artifacts' if checks else 'saved_artifacts_and_author_observations',
            'workspace_unchanged': unchanged, 'passed': unchanged and all(c['exit_code'] == 0 for c in checks)}
