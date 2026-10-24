import { useState } from 'react';
import type { WorkContract } from '../types';
export function WorkAgreementForm({ goal, agreement, taskId, agents, busy, command, onDone, onCancel }: {
  goal: string; agreement?: WorkContract; taskId?: string; agents: { id: string; display_name: string }[]; busy: boolean;
  command: (action: string, data?: Record<string, unknown>) => Promise<boolean>; onDone: () => void; onCancel?: () => void;
}) {
  const [title, setTitle] = useState(agreement?.goal ?? goal);
  const [scope, setScope] = useState(agreement?.scope ?? '当前项目');
  const [criteria, setCriteria] = useState(agreement?.criteria.map(c => c.text).join('\n') ?? '');
  const [owner, setOwner] = useState(agents[0]?.id ?? '');
  const [delivery, setDelivery] = useState(agreement?.delivery ?? 'development');
  return <form className="work-form" onSubmit={e => {
    e.preventDefault();
    const value = { source: agreement?.source ?? title, goal: title, scope, non_goals: agreement?.non_goals ?? '', delivery,
      criteria: criteria.split('\n').map(s => s.trim()).filter(Boolean).map((text, i) => ({ id: `AC-${i + 1}`, text })),
      risks: agreement?.risks ?? ['local'], validation_commands: agreement?.validation_commands ?? [] };
    void command(taskId ? 'revise_work' : 'create_work', taskId ? { task_id: taskId, agreement: value } : { title, owner_id: owner || agents[0]?.id, agreement: value }).then(ok => { if (ok) onDone(); });
  }}>
    <h3>{taskId ? '更新交付约定' : '开始一项新任务'}</h3>
    <label>要交付什么<textarea required value={title} onChange={e => setTitle(e.target.value)} /></label>
    {!taskId && <label>交付负责人<select required value={owner || agents[0]?.id || ''} onChange={e => setOwner(e.target.value)}>{agents.map(a => <option key={a.id} value={a.id}>{a.display_name}</option>)}</select></label>}
    <label>本次范围<input required value={scope} onChange={e => setScope(e.target.value)} /></label>
    <label>怎样算完成（每行一项）<textarea required value={criteria} onChange={e => setCriteria(e.target.value)} /></label>
    {!taskId && <label>交付终点<select value={delivery} onChange={e => setDelivery(e.target.value as typeof delivery)}><option value="document">文档交付</option><option value="development">开发交付</option><option value="online">上线交付</option></select></label>}
    <p className="work-muted">按此范围持续推进；生产发布仍需要你批准具体版本。</p><div className="work-decision-buttons"><button className="work-primary" disabled={busy || !agents.length}>{taskId ? '保存并重新评估' : '创建并推进'}</button>{onCancel && <button type="button" onClick={onCancel}>取消</button>}</div>
  </form>;
}
