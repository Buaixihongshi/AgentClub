"""Long-term agent memory. Markdown files are the authority; SQLite is a
rebuildable index serving the management UI and consistency reports.

Writes go to the file first (atomic replace), then the index; a crash between
the two leaves only a stale index, which sync()/rebuild() repairs from files.
Prompt assembly reads files directly, so it never depends on index freshness.
"""
import hashlib
import os
import re
import tempfile
from pathlib import Path

from backend.app.domain.models import utc_now

from backend.app.storage.repository import Repository

MAX_TITLE = 120
MAX_BODY = 8000
MAX_TAGS = 8
MAX_REFS = 40
FRONTMATTER_KEYS = ('id', 'agent_id', 'title', 'tags', 'status', 'revision',
                    'created_at', 'updated_at', 'source_refs')
_SAFE_ID = re.compile(r'^[A-Za-z0-9_-]+$')


class MemoryError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return utc_now().isoformat()


def _uid() -> str:
    return 'mem_' + hashlib.sha256(os.urandom(16)).hexdigest()[:12]


def _agent_dir(root: Path, agent_id: str) -> Path:
    if not _SAFE_ID.match(agent_id or ''):
        raise MemoryError('角色 ID 无效')
    return root / 'agents' / agent_id


def _parse(text: str) -> dict | None:
    """Parse one memory file: simple ``key: value`` frontmatter plus body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return None
    meta: dict[str, str] = {}
    index = 1
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == '---':
            break
        key, sep, value = line.partition(':')
        if sep and key.strip() in FRONTMATTER_KEYS:
            meta[key.strip()] = value.strip()
    else:
        return None
    body = '\n'.join(lines[index + 1:]).strip('\n')
    if not meta.get('id') or not meta.get('agent_id'):
        return None
    try:
        revision = int(meta.get('revision') or 1)
    except ValueError:
        revision = 1
    return {
        'id': meta['id'], 'agent_id': meta['agent_id'],
        'title': meta.get('title') or '（无标题）',
        'tags': [t for t in (meta.get('tags') or '').split(',') if t],
        'status': meta.get('status') or 'active',
        'revision': max(1, revision),
        'created_at': meta.get('created_at') or '',
        'updated_at': meta.get('updated_at') or '',
        'source_refs': [r for r in (meta.get('source_refs') or '').split(',') if r],
        'body': body,
    }


def _render(entry: dict) -> str:
    lines = ['---']
    lines.append('id: {}'.format(entry['id']))
    lines.append('agent_id: {}'.format(entry['agent_id']))
    lines.append('title: {}'.format(entry['title'].replace('\n', ' ')))
    lines.append('tags: {}'.format(','.join(entry.get('tags') or [])))
    lines.append('status: {}'.format(entry['status']))
    lines.append('revision: {}'.format(entry['revision']))
    lines.append('created_at: {}'.format(entry['created_at']))
    lines.append('updated_at: {}'.format(entry['updated_at']))
    lines.append('source_refs: {}'.format(','.join(entry.get('source_refs') or [])))
    lines.append('---')
    lines.append('')
    lines.append(entry['body'])
    return '\n'.join(lines) + '\n'


def _digest(entry: dict) -> str:
    return hashlib.sha256(_render(entry).encode('utf-8')).hexdigest()


def _summary(entry: dict) -> dict:
    return {k: entry[k] for k in ('id', 'agent_id', 'title', 'tags', 'status', 'revision',
                                  'created_at', 'updated_at')} | {'body_chars': len(entry['body'])}


class MemoryStore:
    def __init__(self, repository: Repository, root: Path):
        self.repository = repository
        self.root = Path(root)

    # ---- authority: files -------------------------------------------------

    def _write_file(self, entry: dict) -> None:
        directory = _agent_dir(self.root, entry['agent_id'])
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / '{}.md'.format(entry['id'])
        raw = _render(entry)
        handle, tmp_name = tempfile.mkstemp(dir=directory, prefix='.tmp-', suffix='.md')
        try:
            with os.fdopen(handle, 'w', encoding='utf-8') as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _read_file(self, agent_id: str, entry_id: str) -> dict | None:
        if not _SAFE_ID.match(entry_id or ''):
            raise MemoryError('记忆条目 ID 无效')
        path = _agent_dir(self.root, agent_id) / '{}.md'.format(entry_id)
        if not path.is_file():
            return None
        return _parse(path.read_text(encoding='utf-8'))

    def _scan(self, agent_id: str) -> list[dict]:
        directory = _agent_dir(self.root, agent_id)
        if not directory.is_dir():
            return []
        entries = []
        for path in sorted(directory.glob('*.md')):
            entry = _parse(path.read_text(encoding='utf-8'))
            if entry:
                entries.append(entry)
        entries.sort(key=lambda e: (e['updated_at'], e['id']), reverse=True)
        return entries

    def _all_files(self) -> list[dict]:
        base = self.root / 'agents'
        if not base.is_dir():
            return []
        entries = []
        for directory in sorted(p for p in base.iterdir() if p.is_dir()):
            entries.extend(self._scan(directory.name))
        return entries

    def _locate(self, entry_id: str) -> dict:
        for entry in self._all_files():
            if entry['id'] == entry_id:
                return entry
        raise MemoryError('记忆条目不存在', 404)

    # ---- index: derived, rebuildable --------------------------------------

    def _upsert_index(self, db, entry: dict, path: Path) -> None:
        db.execute(
            'INSERT INTO memory_entries (id, agent_id, path, title, tags, status, revision,'
            ' digest, body_chars, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
            ' ON CONFLICT(id) DO UPDATE SET agent_id=excluded.agent_id, path=excluded.path,'
            ' title=excluded.title, tags=excluded.tags, status=excluded.status,'
            ' revision=excluded.revision, digest=excluded.digest, body_chars=excluded.body_chars,'
            ' updated_at=excluded.updated_at',
            (entry['id'], entry['agent_id'], str(path), entry['title'],
             ','.join(entry['tags']), entry['status'], entry['revision'], _digest(entry),
             len(entry['body']), entry['updated_at']))

    def _index_row(self, db, entry_id: str):
        return db.execute('SELECT * FROM memory_entries WHERE id=?', (entry_id,)).fetchone()

    # ---- operations --------------------------------------------------------

    def write(self, agent_id: str, *, title: str, body: str, tags=(), source_refs=(),
              status: str = 'active') -> dict:
        title = ' '.join(str(title or '').split())
        body = str(body or '').strip()
        tags = [str(t).strip() for t in tags if str(t).strip()][:MAX_TAGS]
        source_refs = [str(r).strip() for r in source_refs if str(r).strip()][:MAX_REFS]
        if not (1 <= len(title) <= MAX_TITLE):
            raise MemoryError('标题长度须在 1–{} 字符之间'.format(MAX_TITLE))
        if not (1 <= len(body) <= MAX_BODY):
            raise MemoryError('正文长度须在 1–{} 字符之间'.format(MAX_BODY))
        if status not in {'active', 'forgotten'}:
            raise MemoryError('状态无效')
        entry = {'id': _uid(), 'agent_id': agent_id, 'title': title, 'tags': tags,
                 'status': status, 'revision': 1, 'created_at': _now(), 'updated_at': _now(),
                 'source_refs': source_refs, 'body': body}
        self._write_file(entry)
        with self.repository._transaction() as db:
            self._upsert_index(db, entry, _agent_dir(self.root, agent_id) / (entry['id'] + '.md'))
        return entry

    def revise(self, entry_id: str, *, title=None, body=None, tags=None, source_refs=None,
               editor: str = 'agent') -> dict:
        entry = self._locate(entry_id)
        if title is not None:
            title = ' '.join(str(title).split())
            if not (1 <= len(title) <= MAX_TITLE):
                raise MemoryError('标题长度须在 1–{} 字符之间'.format(MAX_TITLE))
            entry['title'] = title
        if body is not None:
            body = str(body).strip()
            if not (1 <= len(body) <= MAX_BODY):
                raise MemoryError('正文长度须在 1–{} 字符之间'.format(MAX_BODY))
            entry['body'] = body
        if tags is not None:
            entry['tags'] = [str(t).strip() for t in tags if str(t).strip()][:MAX_TAGS]
        if source_refs is not None:
            entry['source_refs'] = [str(r).strip() for r in source_refs
                                    if str(r).strip()][:MAX_REFS]
        entry['revision'] += 1
        entry['updated_at'] = _now()
        self._write_file(entry)
        with self.repository._transaction() as db:
            self._upsert_index(db, entry, _agent_dir(self.root, entry['agent_id']) / (entry_id + '.md'))
        return entry

    def set_status(self, entry_id: str, status: str) -> dict:
        if status not in {'active', 'forgotten'}:
            raise MemoryError('状态无效')
        entry = self._locate(entry_id)
        if entry['status'] == status:
            return entry
        entry['status'] = status
        entry['updated_at'] = _now()
        self._write_file(entry)
        with self.repository._transaction() as db:
            self._upsert_index(db, entry, _agent_dir(self.root, entry['agent_id']) / (entry_id + '.md'))
        return entry

    def purge(self, entry_id: str) -> dict:
        entry = self._locate(entry_id)
        (_agent_dir(self.root, entry['agent_id']) / '{}.md'.format(entry_id)).unlink()
        with self.repository._transaction() as db:
            db.execute('DELETE FROM memory_entries WHERE id=?', (entry_id,))
        return {'id': entry_id, 'purged': True}

    def read_entries(self, agent_id: str, *, include_forgotten: bool = False) -> list[dict]:
        return [e for e in self._scan(agent_id) if include_forgotten or e['status'] == 'active']

    def read_entry(self, entry_id: str) -> dict:
        return self._locate(entry_id)

    def brief(self, agent_id: str, *, limit: int = 6, max_chars: int = 2400) -> list[dict]:
        entries = self.read_entries(agent_id)[:limit]
        budget = max_chars
        result = []
        for entry in entries:
            summary = '- {}（r{}）{}'.format(
                entry['title'], entry['revision'],
                ' #{}'.format(' #'.join(entry['tags'])) if entry['tags'] else '')
            remaining = budget - len(summary) - 1
            if remaining <= 0:
                break
            text = entry['body'][:remaining]
            result.append({'id': entry['id'], 'title': entry['title'], 'revision': entry['revision'],
                           'text': text, 'truncated': len(text) < len(entry['body'])})
            budget -= len(summary) + len(text) + 2
        return result

    # ---- consistency -------------------------------------------------------

    def sync(self, *, apply: bool = True) -> dict:
        """Reconcile the index with the Markdown authority.

        Files win: rows are refreshed from files, file-less rows are reported
        (and dropped when applying) instead of resurrecting deleted content.
        """
        files = self._all_files()
        by_id = {e['id']: e for e in files}
        with self.repository._transaction() as db:
            rows = db.execute('SELECT id, digest FROM memory_entries').fetchall()
            indexed = {row['id']: row['digest'] for row in rows}
            missing = sorted(set(indexed) - set(by_id))
            stale = [e for e in files if indexed.get(e['id']) != _digest(e)]
            if apply:
                for entry in stale:
                    self._upsert_index(db, entry,
                                       _agent_dir(self.root, entry['agent_id']) / (entry['id'] + '.md'))
                for entry_id in missing:
                    db.execute('DELETE FROM memory_entries WHERE id=?', (entry_id,))
            counts = db.execute(
                'SELECT agent_id, COUNT(*) AS n FROM memory_entries WHERE status=? GROUP BY agent_id',
                ('active',)).fetchall()
        return {'files': len(files), 'indexed': len(by_id), 'stale': len(stale),
                'missing_files': len(missing), 'applied': apply,
                'active_by_agent': {row['agent_id']: row['n'] for row in counts}}

    def rebuild(self) -> dict:
        with self.repository._transaction() as db:
            db.execute('DELETE FROM memory_entries')
        return self.sync(apply=True)

    def status(self) -> dict:
        return self.sync(apply=False)
