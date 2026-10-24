"""Markdown authority + rebuildable index: writes, revision/forget, consistency."""
import json

import pytest

from backend.app.memory.store import MemoryStore, MemoryError


@pytest.fixture()
def store(repository, temp_settings):
    return MemoryStore(repository, temp_settings.memory_root)


def test_write_creates_authoritative_file_and_index_row(store, temp_settings):
    entry = store.write('chulan', title='用户偏好', body='用户偏好简洁的中文回复。',
                        tags=['偏好'], source_refs=['msg_1'])
    path = temp_settings.memory_root / 'agents' / 'chulan' / f"{entry['id']}.md"
    assert path.is_file()
    text = path.read_text(encoding='utf-8')
    assert text.startswith('---\n')
    assert 'title: 用户偏好' in text
    assert 'status: active' in text
    with store.repository._transaction() as db:
        row = db.execute('SELECT * FROM memory_entries WHERE id=?', (entry['id'],)).fetchone()
    assert row and row['agent_id'] == 'chulan' and row['revision'] == 1


def test_revise_increments_revision_and_updates_both(store):
    entry = store.write('chulan', title='结论', body='第一版结论')
    revised = store.revise(entry['id'], title='结论（修订）', body='第二版结论')
    assert revised['revision'] == 2
    again = store.read_entry(entry['id'])
    assert again['title'] == '结论（修订）' and again['body'] == '第二版结论'


def test_forget_excludes_from_reads_and_restore_recovers(store):
    entry = store.write('chulan', title='临时事实', body='很快会过期')
    store.set_status(entry['id'], 'forgotten')
    assert store.read_entries('chulan') == []
    assert store.read_entries('chulan', include_forgotten=True)[0]['status'] == 'forgotten'
    assert store.brief('chulan') == []
    store.set_status(entry['id'], 'active')
    assert store.read_entries('chulan')[0]['id'] == entry['id']


def test_purge_removes_file_and_index_row(store):
    entry = store.write('chulan', title='将被清除', body='内容')
    result = store.purge(entry['id'])
    assert result['purged'] is True
    assert store.read_entries('chulan') == []
    with pytest.raises(MemoryError):
        store.read_entry(entry['id'])


def test_missing_entry_raises_404(store):
    with pytest.raises(MemoryError) as exc:
        store.revise('mem_missing', body='x')
    assert exc.value.status_code == 404


def test_write_validates_lengths_and_ids(store):
    with pytest.raises(MemoryError):
        store.write('chulan', title='', body='x')
    with pytest.raises(MemoryError):
        store.write('chulan', title='t', body='x' * 8001)
    with pytest.raises(MemoryError):
        store.write('../escape', title='t', body='x')


def test_brief_respects_count_and_char_budget(store):
    for index in range(8):
        store.write('chulan', title=f'条目{index}', body='内容' * 400)
    brief = store.brief('chulan', limit=3, max_chars=1000)
    assert len(brief) <= 3
    assert sum(len(item['text']) for item in brief) <= 1000
    assert any(item['truncated'] for item in brief)


def test_sync_detects_manual_file_edit_and_missing_file(store):
    entry = store.write('chulan', title='手工可改', body='原始内容')
    path = store.root / 'agents' / 'chulan' / f"{entry['id']}.md"
    # Authority edited by hand: index is stale until synced.
    path.write_text(path.read_text(encoding='utf-8').replace('原始内容', '用户手工修正'), encoding='utf-8')
    report = store.status()
    assert report['stale'] == 1
    store.sync(apply=True)
    assert store.status()['stale'] == 0
    assert '用户手工修正' in store.read_entry(entry['id'])['body']
    # File deleted behind the index: row is reported then dropped.
    path.unlink()
    assert store.status()['missing_files'] == 1
    store.sync(apply=True)
    assert store.status()['missing_files'] == 0


def test_crash_between_file_and_index_is_repaired_by_sync(store):
    entry = store.write('chulan', title='崩溃窗口', body='内容')
    with store.repository._transaction() as db:
        db.execute('DELETE FROM memory_entries WHERE id=?', (entry['id'],))
    # Files win; the index is derived and rebuildable.
    report = store.sync(apply=True)
    assert report['files'] == 1
    with store.repository._transaction() as db:
        row = db.execute('SELECT id FROM memory_entries WHERE id=?', (entry['id'],)).fetchone()
    assert row is not None


def test_rebuild_from_scratch_matches_files(store):
    first = store.write('chulan', title='A', body='a')
    second = store.write('chulan', title='B', body='b', tags=['t'])
    store.set_status(second['id'], 'forgotten')
    report = store.rebuild()
    assert report['files'] == 2
    with store.repository._transaction() as db:
        rows = db.execute('SELECT id, status FROM memory_entries').fetchall()
    assert {row['id'] for row in rows} == {first['id'], second['id']}
    assert next(r for r in rows if r['id'] == second['id'])['status'] == 'forgotten'


def test_entries_sorted_by_recency(store):
    old = store.write('chulan', title='旧', body='旧内容')
    new = store.write('chulan', title='新', body='新内容')
    entries = store.read_entries('chulan')
    assert entries[0]['id'] == new['id'] and entries[1]['id'] == old['id']
