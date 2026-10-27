import { useState } from 'react';
import { ArrowRight, CheckCircle2, ChevronRight, Clock3, FileText, MoreHorizontal, AlertCircle } from 'lucide-react';
import type { SharedWorkBoardView, SharedWorkTask } from '../types';
import { currentReview, decisionKinds, decisionMatches, eventNames, pendingDecisions, shortVersion, taskStatus, taskSummary, workActivities, workStates, type WorkActivity } from '../lib/workPresentation';
import { WorkArtifacts } from './WorkArtifacts';
import { WorkDialog } from './WorkDialog';
import { WorkAgreementForm } from './WorkAgreementForm';
import { WorkEnvironment } from './WorkEnvironment';

export function AdaptiveWorkBody({ board, conversationId, goal, agents, busy, error, archived, command, requestedDecision, onDecisionClosed, onDiscuss, taskId, embedded = false, allowRevision = true }: {
  board: SharedWorkBoardView; conversationId: string; goal: string; taskId?: string; embedded?: boolean; allowRevision?: boolean;
  agents: { id: string; display_name: string }[]; busy: boolean; error: string; archived: boolean;
  command: (action: string, data?: Record<string, unknown>) => Promise<boolean>;
  onDiscuss?: () => void;
  requestedDecision?: string | null; onDecisionClosed?: () => void;
}) {
  const roots = board.tasks.filter(t => !t.parent_id);
  const [selected, setSelected] = useState('');
  const [editing, setEditing] = useState(false);
  const [showAgreement, setShowAgreement] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [showEarlier, setShowEarlier] = useState(false);
  const [activity, setActivity] = useState<WorkActivity | null>(null);
  const [decisionId, setDecisionId] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const requestedTask = board.tasks.find(t => t.id === board.decisions?.find(d => d.id === requestedDecision)?.task_id);
  const root = taskId ? roots.find(t => t.id === taskId) : roots.find(t => t.id === requestedTask?.root_id) ?? roots.find(t => t.id === selected) ?? roots.find(t => !['done', 'cancelled'].includes(t.status)) ?? roots[0];
  const agreement = board.contracts.find(c => c.id === root?.contract_id);
  const label = (id: string) => id === 'human' ? '你' : id === 'host' ? '系统' : agents.find(a => a.id === id)?.display_name ?? id;
  const related = root ? board.tasks.filter(t => t.id === root.id || t.root_id === root.id) : [];
  const elsewhere = board.execution?.active.find(t => !related.some(r => r.id === t.task_id));
  const queued = !!root && !root.paused && (elsewhere && root.status === 'ready' || board.execution?.queued && related.some(task => task.status === 'ready' && !task.paused));
  const pending = root ? pendingDecisions(board, root) : [];
  const activities = workActivities(board).filter(e => !root || related.some(t => t.id === e.taskId));
  const priority = (e: WorkActivity) => e.decisionId || e.action === 'review' ? 3 : e.action === 'refine_agreement' || (e.action === 'submit' && board.tasks.some(t => t.id === e.taskId && t.parent_id)) ? 2 : 1;
  const highlighted = new Set([...activities].sort((a, b) => priority(b) - priority(a) || Date.parse(b.createdAt) - Date.parse(a.createdAt)).slice(0, 6).map(e => e.id));
  const active = board.decisions?.find(d => d.id === (requestedDecision || decisionId) && (!taskId || related.some(task => task.id === d.task_id)));
  const closeDecision = () => { setDecisionId(null); onDecisionClosed?.(); };
  const review = root && currentReview(root);
  const safeDecision = active && active.status === 'pending' && decisionMatches(board, active);
  const decisionTask = board.tasks.find(t => t.id === active?.task_id);
  const decisionReview = decisionTask && currentReview(decisionTask);
  return <div className="work-overview">
    {!embedded ? <div className="work-page-heading"><div><h2>{root?.title || '任务'}</h2>{root && <p>负责人 {label(root.owner_id)} <span className={`work-state work-state-${root.status}`}>{taskStatus(root, board)}</span></p>}</div>
      <div className="work-page-tools">{root && <button className="work-text-button" onClick={() => setShowAgreement(true)}>查看需求 <ArrowRight size={15} /></button>}
        <details className="work-actions"><summary aria-label="任务操作"><MoreHorizontal size={22} /></summary><div>
          {!archived && onDiscuss && <button onClick={onDiscuss}>在对话中提出新需求</button>}
          {root && !['done', 'cancelled'].includes(root.status) && <>
            <button disabled={busy} onClick={() => void command(root.paused ? 'resume' : 'pause', { task_id: root.id })}>{root.paused ? '恢复任务' : '暂停任务'}</button>
            <button disabled={busy} onClick={() => void command('cancel', { task_id: root.id })}>取消任务</button>
          </>}
          {allowRevision && root && !root.active_mission_id && root.status !== 'cancelled' && <button disabled={busy} onClick={() => setEditing(true)}>调整需求</button>}
        </div></details>
      </div>
    </div> : <div className="work-embedded-heading"><h2>交付与记录</h2><div>
      {root && <button className="work-text-button" onClick={() => setShowAgreement(true)}>查看需求 <ArrowRight size={15} /></button>}
      {allowRevision && root && !root.active_mission_id && root.status !== 'cancelled' && <button className="work-text-button" disabled={busy} onClick={() => setEditing(true)}>调整需求</button>}
    </div></div>}
    {!taskId && roots.length > 1 && <label className="work-task-picker">当前任务<select value={root?.id} onChange={e => setSelected(e.target.value)}>{roots.map(t => <option key={t.id} value={t.id}>{t.title}</option>)}</select></label>}
    {error && <p className="work-error" role="alert">{error}</p>}
    {root && <>
      {queued && <p className="work-notice" role="status">{elsewhere ? `排队中：另一项工作「${elsewhere.title}」正在执行，完成后会继续。` : '排队中：执行通道正在处理其他工作，已就绪步骤会在空闲后继续。'}</p>}
      {pending.length ? <div className="work-attention-list">{pending.map(d => <div className="work-attention" key={d.id}>
        <Clock3 size={25} strokeWidth={1.6} /><div><h3>待你处理 · {decisionKinds[d.kind] || '任务决定'}</h3><p>{taskSummary(root, board, label)}</p></div>
        <button className="work-primary" onClick={() => setDecisionId(d.id)}>查看并决定 <ArrowRight size={16} /></button>
      </div>)}</div> : <div className={`work-current${root.blocker ? ' needs-attention' : ''}`}>{root.status === 'done' ? <CheckCircle2 size={20} /> : root.blocker ? <AlertCircle size={20} /> : <Clock3 size={20} />}<div><strong>{taskStatus(root, board)}</strong><p>{taskSummary(root, board, label)}</p></div>
        {root.status === 'waiting' && ['execution_failed', 'external', 'recovery', 'release_unknown'].includes(root.waiting_reason ?? '') && <button disabled={busy || !!root.active_mission_id} onClick={() => void command('retry', { task_id: root.id })}>核对后重试</button>}
        {['budget', 'no_progress'].includes(root.waiting_reason ?? '') && <button disabled={busy} onClick={() => void command('adjust_budget', { task_id: root.id, max_attempts: Math.min(200, (root.max_attempts ?? 20) + 10) })}>增加 10 次预算</button>}
      </div>}
      <WorkEnvironment task={root} conversationId={conversationId} busy={busy} command={command} />
      <section className="work-section"><div className="work-section-heading"><h3>关键记录</h3><button className="work-text-button" onClick={() => setShowAll(true)}>全部记录 <ArrowRight size={15} /></button></div>
        {activities.length ? <ol className="work-activity-list">{(showEarlier ? activities : activities.filter(e => highlighted.has(e.id))).map(e => <li key={e.id}><button onClick={() => setActivity(e)}>
          <span className={`work-person work-person-${e.actor}`} aria-hidden="true">{label(e.actor).slice(0, 1)}</span><strong>{label(e.actor)}</strong><span>{e.title}</span><time dateTime={e.createdAt}>{formatTime(e.createdAt)}</time><ChevronRight size={16} />
        </button></li>)}</ol> : <p className="work-muted">任务开始后，关键进展会记录在这里。</p>}
        {activities.length > 6 && <button className="work-text-button" onClick={() => setShowEarlier(!showEarlier)}>{showEarlier ? '收起展开的记录' : `查看全部 ${activities.length} 项关键记录`}</button>}
      </section>
      {root.submission && <section className="work-section"><div className="work-section-heading"><h3>交付物</h3><span className="work-muted">{root.submission.artifact_paths.length} 份文件</span></div><WorkArtifacts conversationId={conversationId} version={root.candidate?.id} paths={root.submission.artifact_paths} /></section>}
      {root.candidate && <details className="work-version"><summary><FileText size={15} /> 当前候选快照 {shortVersion(root.candidate.id)} <span>查看版本依据</span></summary><dl><dt>候选快照</dt><dd>{root.candidate.id}</dd><dt>需求约定</dt><dd>v{agreement?.version} · {agreement?.digest}</dd><dt>复审状态</dt><dd>{review ? `${label(review.reviewer_id)} · ${review.decision === 'approved' ? '当前快照复审通过' : '要求修改'}` : '当前快照尚无有效审核'}</dd><dt>Git 基线</dt><dd>{root.candidate.base_sha}（快照另含未提交内容）</dd></dl></details>}
      {(board.decisions ?? []).some(d => related.some(t => t.id === d.task_id) && d.status !== 'pending') && <details className="work-task-details"><summary>历史决定</summary>{(board.decisions ?? []).filter(d => related.some(t => t.id === d.task_id) && d.status !== 'pending').map(d => <p key={d.id}><button className="work-text-button" onClick={() => setDecisionId(d.id)}>{d.status === 'stale' ? '已失效的决定' : `已选择：${d.options.find(o => o.id === d.option_id)?.text || d.option_id}`}</button></p>)}</details>}
      <details className="work-task-details"><summary>责任、验证与任务设置</summary><p className="work-muted">已执行 {root.attempts_used ?? 0} / {root.max_attempts ?? 20} 次。暂停后，当前动作可能继续收尾，后续动作停止。</p>
        {related.map(task => <TaskEvidence key={task.id} task={task} board={board} label={label} conversationId={conversationId} busy={busy} command={command} />)}
      </details>
    </>}
    {showAgreement && agreement && <WorkDialog title="需求与验收约定" onClose={() => setShowAgreement(false)}><p className="work-muted">约定 v{agreement.version}</p><h3>交付目标</h3><p className="work-prose">{agreement.goal}</p><h3>本次范围</h3><p>{agreement.scope}</p>{agreement.non_goals && <p>本次不做：{agreement.non_goals}</p>}<h3>怎样算完成</h3><ul>{agreement.criteria.map(c => <li key={c.id}>{c.id} · {c.text}</li>)}</ul><details><summary>原始需求与完整标识</summary><p className="work-prose">{agreement.source}</p><code>{agreement.digest}</code></details></WorkDialog>}
    {editing && root && agreement && <WorkDialog title="调整需求" onClose={() => setEditing(false)}><WorkAgreementForm goal={agreement.goal} agreement={agreement} taskId={root.id} agents={agents} busy={busy} command={command} onDone={() => setEditing(false)} /></WorkDialog>}
    {active && <WorkDialog title={decisionKinds[active.kind] || '任务决定'} onClose={closeDecision}>
      <p className="work-decision-question">{active.question}</p>
      <dl className="work-decision-facts"><dt>需求约定</dt><dd>v{board.contracts.find(c => c.id === active.contract_id)?.version}</dd><dt>候选快照</dt><dd>{shortVersion(active.candidate_id)}</dd>{active.environment && <><dt>目标环境</dt><dd>{active.environment}</dd></>}{decisionReview && <><dt>当前审核</dt><dd>{label(decisionReview.reviewer_id)} · {decisionReview.decision === 'approved' ? '通过' : '要求修改'}</dd></>}</dl>
      {active.kind === 'release' && <p className="work-notice"><AlertCircle size={17} />批准仅适用于此快照与目标环境。实际发布由项目发布入口执行，结果会另行核对。</p>}
      {decisionReview && <details><summary>查看当前复审结论</summary><p className="work-prose">{decisionReview.note}</p></details>}
      {active.recommendation && <details><summary>建议与影响</summary><p className="work-prose">{active.recommendation}</p></details>}
      <WorkArtifacts conversationId={conversationId} version={active.candidate_id} paths={active.artifact_paths} />
      {decisionTask?.submission?.limitations && <details><summary>交付时记录的限制</summary><p className="work-prose">{decisionTask.submission.limitations}</p><p className="work-muted">这是提交时的记录；最新审核结论见任务流。</p></details>}
      {(safeDecision || notes[active.id]) && <label className="work-feedback">反馈或修改意见（可选）<textarea readOnly={!safeDecision} value={notes[active.id] ?? ''} onChange={e => setNotes({ ...notes, [active.id]: e.target.value })} /></label>}
      {safeDecision ? <><div className="work-decision-buttons">{active.options.map((option, i) => <button key={option.id} className={i === 0 ? 'work-primary' : ''} disabled={busy || !!decisionTask?.active_mission_id} onClick={() => void command('decide', { task_id: active.task_id, decision_id: active.id, option_id: option.id, note: notes[active.id] ?? '' }).then(ok => { if (ok) closeDecision(); })}>{option.text}</button>)}</div></> : <p className="work-notice" role="status">{active.status === 'answered' ? `已选择：${active.options.find(o => o.id === active.option_id)?.text}` : '此决定对应的版本已失效，请等待新的决定。'}</p>}
      {error && <p role="alert" className="work-error">{error}</p>}
      <details className="work-file-source"><summary>完整版本依据</summary><p>决定 {active.id}</p><p>约定 {active.contract_id}</p><p>候选 {active.candidate_id || '未绑定快照'}</p></details>
    </WorkDialog>}
    {activity && <WorkDialog title={`${label(activity.actor)} · ${activity.title}`} onClose={() => setActivity(null)}><p className="work-muted">{formatTime(activity.createdAt, true)}</p><p className="work-prose">{activity.detail || '此动作已记录。'}</p><dl><dt>来源记录</dt><dd>{activity.id}</dd>{activity.missionId && <><dt>来源任务轮次</dt><dd>{activity.missionId}</dd></>}{activity.contractId && <><dt>需求约定</dt><dd>{activity.contractId}</dd></>}{activity.candidateId && <><dt>对应快照</dt><dd>{activity.candidateId}</dd></>}</dl><p className="work-muted">此处保留当时的结论；当前进展以任务状态和当前版本依据为准。</p></WorkDialog>}
    {showAll && <WorkDialog wide title="完整任务记录" onClose={() => setShowAll(false)}><p className="work-muted">包含执行、重试和系统事件，原始记录均保留。</p><ol className="work-raw-events">{board.events.filter(e => !root || related.some(t => t.id === e.task_id) || !e.task_id).map(e => <li key={e.id}><div><strong>{label(e.actor)} · {eventNames[e.action] || e.action}</strong><time>{formatTime(e.created_at, true)}</time></div>{e.reason && <p className="work-prose">{e.reason}</p>}<small>{e.id}{e.mission_id && ` · ${e.mission_id}`}</small></li>)}</ol></WorkDialog>}
  </div>;
}
function formatTime(value: string, full = false) {
  if (!value || Number.isNaN(Date.parse(value))) return '时间未记录';
  return new Date(value).toLocaleString('zh-CN', full ? { hour12: false } : { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}
function TaskEvidence({ task, board, label, conversationId, busy, command }: { task: SharedWorkTask; board: SharedWorkBoardView; label: (id: string) => string; conversationId: string; busy: boolean; command: (action: string, data?: Record<string, unknown>) => Promise<boolean> }) {
  return <section className="work-evidence-task"><h4>{task.parent_id ? '协作任务' : '整体交付'} · {label(task.owner_id)}</h4><p>{task.title} · {workStates[task.status]}</p>{task.parent_id && <p>{task.blocker || task.next_action || task.progress}</p>}
    {task.transfer && <p>责任移交：{label(task.transfer.to)} · {task.transfer.reason} {task.transfer.status === 'pending' ? <button disabled={busy || !!task.active_mission_id} onClick={() => void command('accept_transfer', { task_id: task.id })}>请目标角色确认接手</button> : '等待确认接手，原负责人继续负责'}</p>}
    {task.parent_id && task.status === 'waiting' && ['execution_failed', 'external', 'recovery'].includes(task.waiting_reason ?? '') && <button disabled={busy || !!task.active_mission_id} onClick={() => void command('retry', { task_id: task.id })}>重试此协作任务</button>}
    {task.evidence.map(e => <details key={e.id}><summary>验证{e.passed ? '通过' : '未通过'} · 约定 v{board.contracts.find(c => c.id === e.contract_id)?.version} · {shortVersion(e.candidate_id)}</summary>
      {e.observations?.map(o => <p className="work-prose" key={o.id}>{o.id} · {o.text}</p>)}{!e.checks.length && <p>依据为文件快照与实际观察，未声明运行自动化检查。</p>}
      {e.checks.map((c, i) => <details key={i}><summary>{c.command.join(' ')} · 退出码 {c.exit_code}</summary><pre className="work-code">{c.output || '（无输出）'}</pre><a href={`/api/conversations/${encodeURIComponent(conversationId)}/work/logs/${encodeURIComponent(c.log_id)}`} target="_blank" rel="noreferrer">完整日志</a></details>)}
      {(e.contract_id !== task.contract_id || e.candidate_id !== task.candidate?.id) && <p className="work-notice">历史依据，仅适用于记录中的版本。</p>}
    </details>)}
    {task.reviews.map(r => <details key={r.id}><summary>{label(r.reviewer_id)} · {r.decision === 'approved' ? '审核通过' : '要求修改'} · {shortVersion(r.candidate_id)}</summary><p className="work-prose">{r.note}</p>{(r.contract_id !== task.contract_id || r.candidate_id !== task.candidate?.id) && <p className="work-notice">历史审核，不覆盖当前版本。</p>}</details>)}
  </section>;
}
