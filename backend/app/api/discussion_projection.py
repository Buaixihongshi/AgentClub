"""Presentation metadata for discussion messages; source records stay intact."""
import json

from backend.app.orchestration.deliberation import is_v2


def _released_audit(repository, mission, state):
    result_ids = list(dict.fromkeys(state.get('opinions', []) + state.get('contributions', [])
        + state.get('reviews', []) + [rid for rid in (state.get('draft_id'), state.get('final_id')) if rid]))
    if not result_ids:
        return {'sources': [], 'reviews': []}
    with repository._transaction() as db:
        placeholders = ','.join('?' for _ in result_ids)
        rows = db.execute(f'SELECT data FROM discussion_results WHERE mission_id = ? AND id IN ({placeholders})',
                          (mission.id, *result_ids)).fetchall()
    results = {result['id']: result for row in rows for result in [json.loads(row['data'])]}
    final = results.get(state['final_id'], {}).get('data', {})
    names = {a['id']: a['display_name'] for a in mission.discussion.profiles}
    known, references, reviews = {}, [], []
    for section in final.get('sections', []):
        references.extend(section.get('source_ids', []))
    dispositions = {d['finding_id']: d for d in final.get('dispositions', [])}
    for result in results.values():
        data = result.get('data', {})
        name = names.get(result.get('agent_id'), result.get('agent_id') or '参与者')
        kind = result['kind']
        if kind == 'opinion':
            known[result['id']] = {'id': result['id'], 'label': f'{name} · 独立观点',
                                   'summary': data.get('position_summary', '')[:300]}
            for claim in data.get('claims', []):
                known[claim['id']] = {'id': claim['id'], 'label': f'{name} · 观点', 'summary': claim['statement'][:300]}
                references.extend(claim.get('source_ids', []))
        elif kind == 'review':
            known[result['id']] = {'id': result['id'], 'label': f'{name} · 复核记录'}
            if data.get('no_findings'):
                reviews.append({'reviewer': name, 'problem': '未发现遗漏或误读；不代表结论已经验证。'})
            for finding in data.get('findings', []):
                item = {'reviewer': name, 'problem': finding['problem']}
                disposition = dispositions.get(finding['id'])
                if disposition:
                    item.update(action=disposition['action'], reason=disposition['reason'])
                reviews.append(item)
                references.extend(finding.get('source_ids', []))
        else:
            known[result['id']] = {'id': result['id'], 'label': f'{name} · ' + ('综合稿' if kind == 'draft' else '分歧补充')}
    for issue_id, issue in state.get('issues', {}).items():
        known[issue_id] = {'id': issue_id, 'label': '分歧事项', 'summary': issue['problem'][:300]}
    material_ids = list(dict.fromkeys(ref for ref in references if ref.startswith('artifact:')))
    if material_ids and state.get('snapshot_id'):
        with repository._transaction() as db:
            placeholders = ','.join('?' for _ in material_ids)
            rows = db.execute(f"SELECT material.key AS id, json_extract(material.value, '$.path') AS path "
                f"FROM discussion_results, json_each(discussion_results.data, '$.materials') AS material "
                f"WHERE discussion_results.mission_id = ? AND discussion_results.id = ? AND material.key IN ({placeholders})",
                (mission.id, state['snapshot_id'], *material_ids)).fetchall()
        for row in rows:
            known[row['id']] = {'id': row['id'], 'label': row['path']}
    return {'sources': [known.get(ref, {'id': ref, 'label': '冻结上下文中的引用'})
                        for ref in dict.fromkeys(references)], 'reviews': reviews}


def annotate_discussion_messages(messages, missions, events, repository=None, audit_cache=None):
    discussions = {mission.id: mission for mission in missions if is_v2(mission)}
    if audit_cache is None:
        audit_cache = {}
    states = {}
    for event in events:
        if event.mission_id in discussions and event.event_type == 'discussion.v2.state':
            states[event.mission_id] = event.payload
    for message in messages:
        mission_id = message.get('mission_id')
        if mission_id not in discussions or message.get('role') != 'agent' or message.get('event_type') != 'message':
            continue
        state = states.get(mission_id, {})
        summaries = state.get('summary_message_ids', [])
        is_final = bool(state.get('final_id') and summaries and message['id'] == summaries[-1])
        metadata = message.setdefault('metadata', {})
        # Quick discussions put each voice in the conversation; full reviews
        # keep the process internal and show only the final result.
        quick = getattr(discussions[mission_id].discussion, 'depth', 'full') == 'quick'
        metadata['discussion_visibility'] = 'final' if is_final else 'opinion' if quick else 'internal'
        if quick and not is_final:
            lease = state.get('leases', {}).get(message.get('turn_id')) or {}
            metadata['discussion_phase'] = lease.get('phase')
        if is_final:
            metadata['discussion_revision_reviewed'] = state.get('final_id') == state.get('draft_id')
            if repository is not None:
                if mission_id not in audit_cache:
                    audit_cache[mission_id] = _released_audit(repository, discussions[mission_id], state)
                metadata['discussion_audit'] = audit_cache[mission_id]
        if is_final and state.get('awaiting_reason') == 'resolve_value_choice':
            metadata['discussion_pending_decision'] = True
    return messages
