"""Content-addressed, durable Git snapshots, including uncommitted content.

Snapshot bytes live in SQLite, not a mutable path presented as a version.
Ignored runtime files are excluded by Git; symlinks cannot escape the workspace.
"""
import base64
import hashlib
import json
import re
import subprocess
from pathlib import Path

from backend.app.work.service import WorkError

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


def git(root, *args):
    try:
        return subprocess.run(['git', *args], cwd=root, capture_output=True,
                              check=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkError('无法读取项目 Git 快照，请检查工作区') from exc


def safe_path(root, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise WorkError('产物必须使用工作区内的相对路径', 422)
    target = root / name
    if not target.resolve().is_relative_to(root) or '.git' in target.relative_to(root).parts:
        raise WorkError('产物路径不能超出工作区或指向 Git 内部', 422)
    return target


def snapshot(root, artifact_paths=()):
    root = Path(root).resolve()
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    names = git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')
    names = set(filter(None, names)) | set(artifact_paths)
    files, total = {}, 0
    for name in sorted(names):
        path = safe_path(root, name)
        if not path.exists():
            continue  # A tracked deletion is represented by its absence.
        if path.is_symlink():
            value = {'symlink': str(path.readlink())}
        elif path.is_file():
            total += path.stat().st_size
            if total > MAX_SNAPSHOT_BYTES:
                raise WorkError('项目快照超过 32 MB，请缩小工作区或排除生成文件')
            raw = path.read_bytes()
            value = {'content': base64.b64encode(raw).decode(), 'executable': bool(path.stat().st_mode & 0o111)}
        else:
            continue
        files[name] = value
    for name in artifact_paths:
        if name not in files:
            raise WorkError(f'产物不存在或不是文件：{name}', 422)
    content = {'base_sha': head, 'files': files}
    raw = json.dumps(content, sort_keys=True, separators=(',', ':')).encode()
    return {'id': 'ws_' + hashlib.sha256(raw).hexdigest(), 'base_sha': head, 'artifact_paths': list(artifact_paths),
            'bytes': raw}


def store(repo, conversation_id, candidate):
    with repo._transaction(immediate=True) as db:
        db.execute('INSERT OR IGNORE INTO work_check_logs VALUES (?, ?, ?)',
                   (candidate['id'], conversation_id, candidate['bytes']))
        # Identical contents are shared blobs, but each execution context must
        # acquire its own reference. Never overwrite the first context's blob.
        db.execute('INSERT OR IGNORE INTO work_snapshot_references VALUES (?, ?)',
                   (conversation_id, candidate['id']))
    return {k: v for k, v in candidate.items() if k != 'bytes'}


def read_bytes(repo, conversation_id, snapshot_id):
    if not isinstance(snapshot_id, str) or not re.fullmatch(r'ws_[0-9a-f]{64}', snapshot_id):
        raise WorkError('快照不存在', 404)
    with repo._transaction() as db:
        row = db.execute('''SELECT output FROM work_check_logs AS blob WHERE blob.id=? AND
            (blob.conversation_id=? OR EXISTS (SELECT 1 FROM work_snapshot_references AS ref
                WHERE ref.snapshot_id=blob.id AND ref.conversation_id=?))''',
            (snapshot_id, conversation_id, conversation_id)).fetchone()
    if not row:
        raise WorkError('快照不存在', 404)
    return bytes(row['output'])


def content(repo, conversation_id, snapshot_id, path):
    value = json.loads(read_bytes(repo, conversation_id, snapshot_id))['files'].get(path)
    if not value or 'content' not in value:
        raise WorkError('快照中没有该文件', 404)
    return base64.b64decode(value['content'])
