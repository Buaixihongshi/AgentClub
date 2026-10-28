import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, GitBranch, ListChecks, MoreHorizontal, Pause, Play, Plus, RefreshCw, Settings2 } from 'lucide-react';
import { listAgents, newRequestId } from '../lib/api';
import { commandTask, createTask, fetchTask, listTasks, type NewTaskPayload } from '../lib/tasks';
import { taskStatus, workStates } from '../lib/workPresentation';
import type { TaskDetail, TaskSummary } from '../types';
import { AdaptiveWorkBody } from './AdaptiveWorkBody';
import { SharedWorkBoard } from './SharedWorkBoard';
import { ConfigureWorkflowDialog, NewTaskDialog } from './NewTaskDialog';
import type { TaskAgent } from './TaskOrchestrationForm';
import { WorkflowGraph } from './WorkflowGraph';
import { UnitySelect } from './UnitySelect';
import './WorkWorkspace.css';
import './TasksWorkspace.css';

type Filter = 'all' | 'active' | 'attention' | 'done';
function summaryStatus(task: TaskSummary) {
  if (task.status === 'done') return '已交付';
  if (task.status === 'cancelled') return '已取消';
  if (task.paused) return '已暂停';
  if (task.pending_decisions) return '待你决定';
  return workStates[task.status] || task.status;
}
export function TasksWorkspace({ selectedTaskId, onSelectTask, onOpenConversation, agents: providedAgents, active = true }: {
  selectedTaskId?: string; onSelectTask: (id: string) => void; onOpenConversation: (id: string) => void;
  agents?: TaskAgent[]; active?: boolean;
}) {
  const [tasks, setTasks] = useState<TaskSummary[]>([]), [detail, setDetail] = useState<TaskDetail | null>(null);
  const [agents, setAgents] = useState<TaskAgent[]>(providedAgents || []);
  const [agentsLoading, setAgentsLoading] = useState(!providedAgents), [agentError, setAgentError] = useState('');
  const [filter, setFilter] = useState<Filter>('all'), [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false), [configuring, setConfiguring] = useState(false);
  const [error, setError] = useState(''), [listError, setListError] = useState(''), [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const selectedRef = useRef(selectedTaskId); selectedRef.current = selectedTaskId;
  const onSelectRef = useRef(onSelectTask); onSelectRef.current = onSelectTask;
  const detailRef = useRef(detail); detailRef.current = detail;
  const listRequest = useRef(0), updating = useRef(false), commandKeys = useRef(new Map<string, string>());
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const refreshAgents = useCallback(async () => {
    setAgentsLoading(true); setAgentError('');
    try {
      const next = await listAgents();
      if (mounted.current) { setAgents(next); setAgentError(next.length ? '' : '还没有可用伙伴，请先完成角色配置。'); }
    } catch (reason) { if (mounted.current) setAgentError(reason instanceof Error ? reason.message : '伙伴列表读取失败，请重试。'); }
    finally { if (mounted.current) setAgentsLoading(false); }
  }, []);
  useEffect(() => {
    if (providedAgents) { setAgents(providedAgents); setAgentsLoading(false); setAgentError(providedAgents.length ? '' : '还没有可用伙伴，请先完成角色配置。'); }
    else void refreshAgents();
  }, [providedAgents, refreshAgents]);
  const refreshList = useCallback(async () => {
    const request = ++listRequest.current;
    try {
      const next = await listTasks();
      if (!mounted.current || request !== listRequest.current) return;
      setTasks(next); setListError('');
      if (!selectedRef.current && next.length) onSelectRef.current(next[0].id);
    } catch (reason) {
      if (mounted.current && request === listRequest.current) setListError(reason instanceof Error ? reason.message : '任务列表同步失败');
    } finally { if (mounted.current && request === listRequest.current) setLoading(false); }
  }, []);
  useEffect(() => {
    if (!active) return;
    void refreshList();
    const timer = window.setInterval(() => void refreshList(), 3000);
    return () => { window.clearInterval(timer); listRequest.current++; };
  }, [active, refreshList]);
  useEffect(() => {
    setDetail(null); setError(''); setLoadError(''); setConfiguring(false);
  }, [selectedTaskId]);
  const refreshDetail = useCallback(async (id: string) => {
    try {
      const next = await fetchTask(id);
      if (!mounted.current || selectedRef.current !== id) return;
      setDetail(previous => previous?.task.id === next.task.id && previous.board.revision > next.board.revision ? previous : next);
      setLoadError('');
    } catch (reason) { if (mounted.current && selectedRef.current === id) setLoadError(reason instanceof Error ? reason.message : '任务详情同步失败'); }
  }, []);
  useEffect(() => {
    if (!selectedTaskId || !active) return;
    void refreshDetail(selectedTaskId);
    const timer = window.setInterval(() => void refreshDetail(selectedTaskId), 3000);
    return () => window.clearInterval(timer);
  }, [selectedTaskId, active, refreshDetail]);
  const refreshLegacy = useCallback(() => {
    if (selectedRef.current) void refreshDetail(selectedRef.current);
    void refreshList();
  }, [refreshDetail, refreshList]);
  async function command(action: string, data: Record<string, unknown> = {}) {
    const current = detailRef.current;
    if (!current || updating.current || current.task.id !== selectedRef.current) return false;
    const taskId = current.task.id;
    const signature = JSON.stringify([taskId, action, current.board.revision, data]);
    if (!commandKeys.current.has(signature)) commandKeys.current.set(signature, newRequestId('task-command'));
    updating.current = true; setBusy(true); setError('');
    try {
      const next = await commandTask(taskId, action, current.board.revision, data, commandKeys.current.get(signature));
      if (mounted.current && selectedRef.current === taskId) setDetail(previous => previous?.task.id === next.task.id && previous.board.revision > next.board.revision ? previous : next);
      void refreshList(); return true;
    } catch (reason) {
      if (mounted.current && selectedRef.current === taskId) {
        setError(reason instanceof Error ? reason.message : '更新失败，请重试');
        await refreshDetail(taskId);
      }
      return false;
    } finally { updating.current = false; if (mounted.current) setBusy(false); }
  }
  async function saveTask(payload: NewTaskPayload) {
    const next = await createTask(payload);
    if (!mounted.current) return;
    setCreating(false); onSelectRef.current(next.task.id); void refreshList();
  }
  const filtered = tasks.filter(task => {
    const match = filter === 'all' || filter === 'done' && ['done', 'cancelled'].includes(task.status) || filter === 'attention' && (!!task.pending_decisions || ['waiting', 'blocked'].includes(task.status)) || filter === 'active' && !['done', 'cancelled'].includes(task.status);
    return match && task.title.toLowerCase().includes(query.toLowerCase());
  });
  const root = detail && detail.task.id === selectedTaskId ? detail.task : null;
  const isLegacy = detail?.protocol === 'legacy';
  const canConfigure = !isLegacy && !!root?.paused && !root.active_mission_id && !root.attempts.length && !(root.attempts_used || 0) && !['done', 'cancelled'].includes(root.status);
  const label = (id: string) => agents.find(a => a.id === id)?.display_name || id;
  const archived = tasks.find(task => task.id === selectedTaskId)?.status === 'cancelled';
  return <div className="tasks-workspace">
    <nav className="task-list" aria-label="任务列表"><div className="task-list-heading"><h2>任务</h2><button type="button" className="conv-new-btn" disabled={agentsLoading || !agents.length} onClick={() => setCreating(true)}><Plus size={14} />{agentsLoading ? '读取伙伴…' : '新建任务'}</button></div>
      <div className="task-list-filters"><label className="task-search"><span className="sr-only">搜索任务</span><input aria-label="搜索任务" placeholder="搜索任务" value={query} onChange={e => setQuery(e.target.value)} /></label><UnitySelect<Filter> label="筛选任务" value={filter} onChange={setFilter} options={[{ value: 'all', label: '全部任务' }, { value: 'active', label: '进行中' }, { value: 'attention', label: '待处理' }, { value: 'done', label: '已结束' }]} /></div>
      {agentError && <div className="task-list-error" role="alert"><p>{agentError}</p><button onClick={() => void refreshAgents()}><RefreshCw size={13} />重新读取伙伴</button></div>}
      {listError && <div className="task-list-error" role="status"><p>{listError}</p><button onClick={() => void refreshList()}><RefreshCw size={13} />重试</button></div>}
      <ul className="task-list-items">{filtered.map(task => <li key={task.id}><button className={`task-list-item${selectedTaskId === task.id ? ' is-active' : ''}`} aria-current={selectedTaskId === task.id ? 'true' : undefined} onClick={() => onSelectTask(task.id)}><div><strong>{task.title}</strong>{task.pending_decisions > 0 && <span className="task-pending-count" aria-label={`${task.pending_decisions} 项待决定`}>{task.pending_decisions}</span>}</div><span>{label(task.owner_id)} · {summaryStatus(task)}</span><small>{task.protocol === 'legacy' ? '历史共享任务' : task.orchestration.mode === 'workflow' ? `${task.orchestration.steps.length} 个流程步骤` : '自主编排'}</small></button></li>)}</ul>
      {!filtered.length && !loading && <p className="task-list-empty">{tasks.length ? '没有匹配的任务' : '还没有任务'}</p>}{loading && <p className="task-list-empty" role="status">正在读取任务…</p>}
      <div className="task-list-footer"><ListChecks size={15} /><span>{tasks.length} 项任务 · 独立管理</span></div>
    </nav>
    <section className="task-main" aria-label="任务详情">
      {!selectedTaskId ? <div className="task-empty"><span className="task-empty-icon"><GitBranch size={30} strokeWidth={1.5} /></span><h2>把一件事交给团队</h2><p>明确目标，选择自主编排或指定流程。<br />任务有自己的进度、决定和交付记录。</p><button className="work-primary" disabled={agentsLoading || !agents.length} onClick={() => setCreating(true)}><Plus size={16} />新建任务</button></div> : !root || !detail ? <div className="task-empty" role="status"><h2>{loadError ? '任务暂时无法读取' : '正在读取任务…'}</h2>{loadError && <><p>{loadError}</p><button onClick={() => void refreshDetail(selectedTaskId)}><RefreshCw size={15} />重新读取</button></>}</div> : detail.protocol === 'legacy' ? <>
        <header className="task-detail-heading"><div><span className="task-section-eyebrow">任务 / 历史共享任务</span><h1>{root.title}</h1><p>保留原有约定、控制和证据</p></div>{detail.source_conversation_id && <button className="task-secondary" onClick={() => onOpenConversation(detail.source_conversation_id!)}>查看来源会话</button>}</header>
        <div className="task-legacy-board"><SharedWorkBoard key={root.id} conversationId={detail.execution_conversation_id} goal={root.title} archived={detail.archived || false} defaultOpen onUpdate={refreshLegacy} /></div>
      </> : <>
        <header className="task-detail-heading"><div><span className="task-section-eyebrow">任务 / {detail.orchestration.mode === 'workflow' ? 'WORKFLOW' : '自主编排'}</span><h1>{root.title}</h1><p><span>负责人 {label(root.owner_id)}</span><span className={`work-state work-state-${root.status}`}>{taskStatus(root, detail.board)}</span></p></div><div className="task-heading-actions"><button type="button" className="task-secondary" disabled={!canConfigure || busy} title={canConfigure ? '调整步骤与依赖' : '仅暂停且尚未执行的任务可调整编排'} onClick={() => setConfiguring(true)}><Settings2 size={15} />调整编排</button>{!['done', 'cancelled'].includes(root.status) && <button type="button" className="work-primary" disabled={busy} onClick={() => void command(root.paused ? 'resume' : 'pause', { task_id: root.id })}>{root.paused ? <Play size={15} /> : <Pause size={15} />}{root.paused ? root.attempts.length ? '恢复任务' : '开始任务' : '暂停任务'}</button>}<details className="task-extra-actions"><summary aria-label="更多任务操作"><MoreHorizontal size={20} /></summary><div>{!['done', 'cancelled'].includes(root.status) && <button disabled={busy} onClick={() => void command('cancel', { task_id: root.id })}>取消任务</button>}{detail.source_conversation_id && <button onClick={() => onOpenConversation(detail.source_conversation_id!)}>查看来源会话<ArrowRight size={14} /></button>}</div></details></div></header>
        {(error || loadError) && <div role="alert" className="task-detail-error">{error || loadError}<button onClick={() => void refreshDetail(root.id)}><RefreshCw size={14} />同步最新状态</button></div>}
        {root.paused && <div className="task-paused-note"><Pause size={14} /><span>{root.attempts.length ? '后续执行已暂停，当前动作可能仍在收尾。' : '任务已保存，点击「开始任务」后才会执行。'}</span></div>}
        <WorkflowGraph key={root.id} detail={detail} agents={agents} />
        <div className="task-delivery-body"><AdaptiveWorkBody key={root.id} embedded allowRevision={detail.orchestration.mode !== 'workflow'} taskId={root.id} board={detail.board} conversationId={detail.execution_conversation_id} goal={detail.board.contracts.find(c => c.id === root.contract_id)?.goal || root.title} agents={agents} busy={busy} error={error} archived={archived} command={command} onDiscuss={detail.source_conversation_id ? () => onOpenConversation(detail.source_conversation_id!) : undefined} /></div>
      </>}
    </section>
    {creating && <NewTaskDialog agents={agents} onClose={() => setCreating(false)} onSave={saveTask} />}
    {configuring && detail && root && <ConfigureWorkflowDialog key={root.id} agents={agents} ownerId={root.owner_id} initial={detail.orchestration} onClose={() => setConfiguring(false)} onSave={value => command('configure_workflow', { orchestration: value })} />}
  </div>;
}
