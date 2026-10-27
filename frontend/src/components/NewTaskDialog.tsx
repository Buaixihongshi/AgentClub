import { useEffect, useRef, useState } from 'react';
import { LockKeyhole, Save } from 'lucide-react';
import type { TaskOrchestration, WorkContract } from '../types';
import type { NewTaskPayload } from '../lib/tasks';
import { newRequestId } from '../lib/api';
import { WorkDialog } from './WorkDialog';
import { TaskOrchestrationForm, taskWorkflowTemplate, workflowError, type TaskAgent } from './TaskOrchestrationForm';

export function NewTaskDialog({ agents, onClose, onSave }: { agents: TaskAgent[]; onClose: () => void; onSave: (payload: NewTaskPayload) => Promise<void> }) {
  const [title, setTitle] = useState('');
  const [goal, setGoal] = useState('');
  const [scope, setScope] = useState('当前项目');
  const [criteria, setCriteria] = useState('');
  const [owner, setOwner] = useState(agents[0]?.id || '');
  const [delivery, setDelivery] = useState<NonNullable<WorkContract['delivery']>>('development');
  const [orchestration, setOrchestration] = useState<TaskOrchestration>(() => taskWorkflowTemplate(agents[0]?.id || ''));
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const operations = useRef(new Map<string, string>());
  useEffect(() => {
    const fallback = agents[0]?.id;
    if (!fallback) return;
    setOwner(previous => previous || fallback);
    setOrchestration(previous => previous.steps.some(step => !step.owner_id)
      ? { ...previous, steps: previous.steps.map(step => ({ ...step, owner_id: step.owner_id || fallback })) } : previous);
  }, [agents]);
  return <WorkDialog title="新建任务" wide onClose={() => { if (!busy) onClose(); }}>
    <form className="work-form task-create-form" onSubmit={e => {
      e.preventDefault();
      if (!title.trim() || !goal.trim() || !scope.trim()) { setError('请填写任务名称、交付目标和本次范围。'); return; }
      const invalid = workflowError(orchestration);
      if (invalid) { setError(invalid); return; }
      const criteriaValues = criteria.split('\n').map(s => s.trim()).filter(Boolean).map((text, i) => ({ id: `AC-${i + 1}`, text }));
      if (!criteriaValues.length) { setError('请至少填写一项验收条件。'); return; }
      const payload: NewTaskPayload = { title: title.trim(), owner_id: owner || agents[0]?.id,
        agreement: { source: goal.trim(), goal: goal.trim(), scope: scope.trim(), non_goals: '', criteria: criteriaValues, validation_commands: [], risks: ['local'], delivery }, orchestration, paused: true };
      const key = JSON.stringify(payload);
      if (!operations.current.has(key)) operations.current.set(key, newRequestId('create-task'));
      payload.operation_id = operations.current.get(key);
      setBusy(true); setError('');
      void onSave(payload).catch(reason => setError(reason instanceof Error ? reason.message : '保存失败，请重试')).finally(() => setBusy(false));
    }}>
      <p className="task-form-intro">先保存目标和流程，确认后再开始执行。任务可以独立于会话存在。</p>
      <div className="task-step-fields"><label>任务名称<input required maxLength={200} value={title} onChange={e => setTitle(e.target.value)} /></label><label>整体负责人<select required value={owner || agents[0]?.id || ''} onChange={e => setOwner(e.target.value)}>{agents.map(a => <option key={a.id} value={a.id}>{a.display_name}</option>)}</select></label></div>
      <label>要交付什么<textarea required value={goal} onChange={e => setGoal(e.target.value)} placeholder="描述目标、使用场景和预期结果" /></label>
      <div className="task-step-fields"><label>本次范围<input required value={scope} onChange={e => setScope(e.target.value)} /></label><label>交付终点<select value={delivery} onChange={e => setDelivery(e.target.value as typeof delivery)}><option value="document">文档交付</option><option value="development">开发交付</option><option value="online">开发后申请上线</option></select></label></div>
      <label>怎样算完成（每行一项）<textarea required value={criteria} onChange={e => setCriteria(e.target.value)} /></label>
      <TaskOrchestrationForm value={orchestration} onChange={setOrchestration} agents={agents} ownerId={owner || agents[0]?.id || ''} disabled={busy} />
      <p className="work-muted">保存后任务保持暂停。开始执行是单独操作；上线仍需批准具体交付版本。</p>
      {error && <p role="alert" className="work-error">{error}</p>}
      <div className="work-decision-buttons"><button type="submit" className="work-primary" disabled={busy || !agents.length}>{busy ? '正在保存…' : '保存任务'}</button><button type="button" disabled={busy} onClick={onClose}>取消</button></div>
    </form>
  </WorkDialog>;
}
export function ConfigureWorkflowDialog({ agents, ownerId, initial, onClose, onSave }: {
  agents: TaskAgent[]; ownerId: string; initial: TaskOrchestration; onClose: () => void; onSave: (value: TaskOrchestration) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<TaskOrchestration>(() => structuredClone(initial));
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  return <WorkDialog title="调整任务编排" wide className="flow-edit-dialog" onClose={() => { if (!busy) onClose(); }}><form className="work-form flow-edit-form" onSubmit={e => {
    e.preventDefault(); const invalid = workflowError(draft); if (invalid) { setError(invalid); return; }
    setBusy(true); setError('');
    const config: TaskOrchestration = { ...draft, steps: draft.steps.map(({ task_id: _binding, ...step }) => step) };
    void onSave(config).then(ok => { if (ok) onClose(); else setError('未能保存编排，请核对任务状态后重试。草稿已保留。'); }).catch(reason => setError(reason instanceof Error ? reason.message : '保存失败，草稿已保留')).finally(() => setBusy(false));
  }}>
    <div className="flow-edit-body">
      <p className="flow-save-note"><LockKeyhole size={14} aria-hidden="true" />仅暂停且尚未执行的任务可以调整。保存后仍保持暂停。</p>
      <TaskOrchestrationForm value={draft} onChange={setDraft} agents={agents} ownerId={ownerId} disabled={busy} />
    </div>
    <footer className="flow-edit-footer">
      <div aria-live="polite">{error ? <p className="work-error" role="alert">{error}</p> : <span>保存编排不会启动任务</span>}</div>
      <div className="work-decision-buttons"><button type="button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="work-primary" disabled={busy}><Save size={14} aria-hidden="true" />{busy ? '正在保存…' : '保存编排'}</button></div>
    </footer>
  </form></WorkDialog>;
}
