import { useEffect, useState } from 'react';
import { fetchSharedWork, listAgents, updateSharedWork } from '../lib/api';
import type { SharedWorkBoardView } from '../types';
import './SharedWorkBoard.css';
import { AdaptiveWorkBody } from './AdaptiveWorkBody';

const statuses = { ready: '待执行', running: '执行中', validating: '宿主验证中', review: '待审核', done: '已完成', blocked: '已阻塞', waiting: '等待条件', cancelled: '已取消' };
const actions: Record<string, string> = {
  propose_contract: '保存约定草案', activate_contract: '启用约定', create_task: '分派任务',
  start: '启动自主推进', pause: '暂停后续执行', retry: '重试任务', progress: '保存进度',
  submit: '提交候选版本', review: '提交审核结论', block: '记录阻塞', claimed: '领取任务',
  validation_started: '运行约定验证', attempt_finished: '保存本轮结果', recovered: '恢复中断记录',
  settled: '本轮推进结束', budget_exhausted: '执行预算耗尽', workspace_changed: '工作区已变化',
};

export function SharedWorkBoard({ conversationId, goal, archived = false, onUpdate, defaultOpen = false }: { conversationId: string; goal: string; archived?: boolean; onUpdate?: () => void; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [board, setBoard] = useState<SharedWorkBoardView | null>(null);
  const [agents, setAgents] = useState<Awaited<ReturnType<typeof listAgents>>>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [source, setSource] = useState(goal);
  const [outcome, setOutcome] = useState(goal);
  const [scope, setScope] = useState('');
  const [nonGoals, setNonGoals] = useState('');
  const [criteria, setCriteria] = useState('');
  const [commands, setCommands] = useState('[["python3", "-m", "pytest", "backend/tests", "-q"]]');
  const [title, setTitle] = useState('');
  const [contractId, setContractId] = useState('');
  const [owner, setOwner] = useState('');
  const [reviewer, setReviewer] = useState('');
  const [selectedCriteria, setSelectedCriteria] = useState<string[]>([]);
  const [dependencies, setDependencies] = useState<string[]>([]);

  useEffect(() => { if (defaultOpen) setOpen(true); }, [defaultOpen]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const refresh = () => fetchSharedWork(conversationId).then(next => {
      if (!cancelled) setBoard(prev => prev && prev.revision > next.revision ? prev : next);
    }).catch(err => { if (!cancelled) setError(String(err.message)); });
    void refresh();
    void listAgents().then(value => { if (!cancelled) setAgents(value); }).catch(() => {});
    const timer = window.setInterval(refresh, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [open, conversationId]);

  useEffect(() => {
    if (board) onUpdate?.();
  }, [board?.revision, onUpdate]);

  async function command(action: string, data: Record<string, unknown> = {}) {
    if (!board || busy || (archived && board.protocol !== 'adaptive_v1')) return false;
    setBusy(true); setError('');
    try {
      const next = await updateSharedWork(conversationId, action, board.revision, data);
      setBoard(prev => prev && prev.revision > next.revision ? prev : next);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : '更新失败');
      void fetchSharedWork(conversationId).then(setBoard).catch(() => {});
      return false;
    } finally { setBusy(false); }
  }

  const contract = board?.contracts.find(c => c.id === contractId) ?? board?.contracts[board.contracts.length - 1];
  const label = (id: string) => id === 'human' ? '用户' : id === 'host' ? '系统' : agents.find(a => a.id === id)?.display_name ?? id;
  const toggle = (list: string[], value: string) => list.includes(value) ? list.filter(item => item !== value) : [...list, value];
  const disabled = busy || archived;
  return <details className="shared-work" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>共享开发任务 <span>约定 · 责任 · 证据</span></summary>
    {open && board?.protocol === 'adaptive_v1' ? <AdaptiveWorkBody board={board} conversationId={conversationId} goal={goal}
      agents={agents} busy={busy} error={error} archived={archived} command={command} /> : open && <div className="shared-work-body">
      <p>把需求保存为开发约定，分配执行人与独立审核人。进度和证据跨回合保留。</p>
      {error && <p role="alert" className="error-banner">{error}</p>}
      {!board ? <p>正在读取共享任务…</p> : <>
        <div className="work-toolbar">
          <span>{board.enabled ? '自主推进已启动' : '自主推进已暂停'} · 版本 {board.revision}</span>
          <button disabled={disabled} onClick={() => {
            if (!editing && contract) {
              setSource(contract.source); setOutcome(contract.goal); setScope(contract.scope);
              setNonGoals(contract.non_goals); setCriteria(contract.criteria.map(c => c.text).join('\n'));
              setCommands(JSON.stringify(contract.validation_commands));
            }
            setEditing(!editing);
          }}>{editing ? '收起约定编辑' : '编写开发约定'}</button>
          {board.enabled
            ? <button disabled={disabled} onClick={() => void command('pause')}>暂停后续执行</button>
            : <button disabled={disabled || !board.active_contract_id} onClick={() => void command('start')}>启动执行与本地提交</button>}
        </div>
        <small>工作区：{board.workspace_root}。启动后允许角色执行约定命令并提交本任务改动；暂停在回合结束后生效。</small>
        {(editing || board.contracts.length === 0) && <form className="work-form" onSubmit={event => {
          event.preventDefault();
          let parsed: unknown;
          try { parsed = JSON.parse(commands); } catch { setError('验证命令请填写 JSON 参数数组，例如 [["npm", "test"]]'); return; }
          void command('propose_contract', { source, goal: outcome, scope, non_goals: nonGoals,
            criteria: criteria.split('\n').map(s => s.trim()).filter(Boolean).map((text, i) => ({ id: `AC-${i + 1}`, text })),
            validation_commands: parsed }).then(ok => { if (ok) setEditing(false); });
        }}>
          <label>原始需求<textarea required value={source} onChange={e => setSource(e.target.value)} /></label>
          <label>预期结果<textarea required value={outcome} onChange={e => setOutcome(e.target.value)} /></label>
          <label>开发范围<input required value={scope} onChange={e => setScope(e.target.value)} /></label>
          <label>本次不做<input required value={nonGoals} onChange={e => setNonGoals(e.target.value)} /></label>
          <label>验收项（每行一项）<textarea required value={criteria} onChange={e => setCriteria(e.target.value)} /></label>
          <label>验证命令（JSON 参数数组，不使用 shell）<textarea required value={commands} onChange={e => setCommands(e.target.value)} /></label>
          <button disabled={disabled}>保存新版本草案</button>
        </form>}
        {board.contracts.map(c => <details className="work-contract" key={c.id}>
          <summary>开发约定 v{c.version} · {c.goal} · {c.id === board.active_contract_id ? '当前启用' : '未启用'}</summary>
          <p>原始需求：{c.source}</p><p>范围：{c.scope}</p><p>本次不做：{c.non_goals}</p>
          <ul>{c.criteria.map(item => <li key={item.id}>{item.id}：{item.text}</li>)}</ul>
          <pre>{JSON.stringify(c.validation_commands, null, 2)}</pre>
          <small>约定摘要：{c.digest}</small>
          {c.id !== board.active_contract_id && <button disabled={disabled} onClick={() => void command('activate_contract', { contract_id: c.id })}>启用 v{c.version} 约定</button>}
        </details>)}
        {contract && <form className="work-form" onSubmit={event => {
          event.preventDefault();
          void command('create_task', { title, contract_id: contract.id, owner_id: owner, reviewer_id: reviewer,
            criterion_ids: selectedCriteria, depends_on: dependencies }).then(ok => { if (ok) { setTitle(''); setDependencies([]); } });
        }}>
          <label>任务所属约定<select value={contract.id} onChange={e => { setContractId(e.target.value); setSelectedCriteria([]); setDependencies([]); }}>
            {board.contracts.map(c => <option value={c.id} key={c.id}>v{c.version} · {c.goal}</option>)}
          </select></label>
          <label>任务名称<input required value={title} onChange={e => setTitle(e.target.value)} /></label>
          <div className="work-roles">
            <label>执行人<select required value={owner} onChange={e => setOwner(e.target.value)}><option value="">选择执行人</option>{agents.map(a => <option key={a.id} value={a.id}>{a.display_name}</option>)}</select></label>
            <label>独立审核人<select required value={reviewer} onChange={e => setReviewer(e.target.value)}><option value="">选择审核人</option>{agents.filter(a => a.id !== owner).map(a => <option key={a.id} value={a.id}>{a.display_name}</option>)}</select></label>
          </div>
          <fieldset><legend>覆盖的验收项</legend>{contract.criteria.map(c => <label className="work-check" key={c.id}><input type="checkbox" checked={selectedCriteria.includes(c.id)} onChange={() => setSelectedCriteria(toggle(selectedCriteria, c.id))} />{c.id}：{c.text}</label>)}</fieldset>
          {board.tasks.some(t => t.contract_id === contract.id) && <fieldset><legend>前置任务</legend>{board.tasks.filter(t => t.contract_id === contract.id).map(t => <label className="work-check" key={t.id}><input type="checkbox" checked={dependencies.includes(t.id)} onChange={() => setDependencies(toggle(dependencies, t.id))} />{t.title}</label>)}</fieldset>}
          <button disabled={disabled || !selectedCriteria.length || owner === reviewer}>保存共享任务</button>
        </form>}
        <div className="work-tasks">{board.tasks.map(task => <article key={task.id} className="work-task">
          <strong>{task.title}</strong><span className={`work-status work-status-${task.status}`}>{statuses[task.status]}</span>
          <p>{label(task.owner_id)} 执行 → {label(task.reviewer_id)} 审核 · {task.criterion_ids.join('、')} · 约定 v{board.contracts.find(c => c.id === task.contract_id)?.version}</p>
          {task.depends_on.length > 0 && <p>依赖：{task.depends_on.map(id => board.tasks.find(t => t.id === id)?.title ?? id).join('、')}</p>}
          {task.progress && <p>最近进度：{task.progress}</p>}
          {task.blocker && <p role="status">阻塞原因：{task.blocker}</p>}
          {task.status === 'blocked' && task.contract_id === board.active_contract_id && <button disabled={disabled || Boolean(task.active_mission_id)} onClick={() => void command('retry', { task_id: task.id })}>重试任务</button>}
          <details><summary>执行与审核证据（{task.attempts.length} 次执行）</summary>
            {task.attempts.map(a => <p key={a.mission_id}>{label(a.agent_id)} · {a.stage === 'review' ? '审核' : '执行'} · {a.outcome ?? '进行中'} · {a.mission_id}</p>)}
            {task.evidence.map(e => <div key={e.id}><p>验证：{e.passed ? '通过' : '未通过'} · Git {e.commit_sha} · {e.criterion_ids.join('、')}</p>
              {e.checks.map((check, i) => <details key={i}><summary>{check.command.join(' ')} · 退出码 {check.exit_code}</summary><pre>{check.output || '（无输出）'}</pre>{check.truncated && <p>此处仅展示末尾 24 KB。</p>}<a href={`/api/conversations/${encodeURIComponent(conversationId)}/work/logs/${encodeURIComponent(check.log_id)}`} target="_blank" rel="noreferrer">查看完整验证日志</a><small>SHA-256：{check.output_sha256}</small></details>)}
            </div>)}
            {task.reviews.map(r => <div key={r.id}><p>{label(r.reviewer_id)}：{r.decision === 'approved' ? '审核通过' : '要求修改'} · Git {r.commit_sha}</p><p>{r.note}</p>
              {board.current_head && board.current_head !== r.commit_sha && <p>这是历史版本的审核，当前代码版本已变化。</p>}</div>)}
          </details>
        </article>)}</div>
        <details><summary>责任与状态变更记录（{board.events.length}）</summary>{board.events.map(e => <p key={e.id}>{new Date(e.created_at).toLocaleString('zh-CN', { hour12: false })} · {label(e.actor)} · {actions[e.action] ?? e.action}{e.reason ? `：${e.reason}` : ''}</p>)}</details>
      </>}
    </div>}
  </details>;
}
