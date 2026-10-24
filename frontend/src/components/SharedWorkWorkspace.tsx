import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, ArrowRight, CheckCircle2, Clock3, RefreshCw } from 'lucide-react';
import { fetchSharedWork, listAgents, updateSharedWork } from '../lib/api';
import type { SharedWorkBoardView } from '../types';
import { pendingDecisions, taskStatus, taskSummary } from '../lib/workPresentation';
import { AdaptiveWorkBody } from './AdaptiveWorkBody';
import { WorkProposalCard } from './WorkProposalCard';
import { SharedWorkBoard } from './SharedWorkBoard';
import './WorkWorkspace.css';

export function SharedWorkWorkspace({ conversationId, goal, archived, onUpdate, chat, composer }: {
  conversationId: string; goal: string; archived: boolean; onUpdate?: () => void; chat: ReactNode; composer: ReactNode;
}) {
  const [tab, setTab] = useState<'chat' | 'work'>('chat');
  const [board, setBoard] = useState<SharedWorkBoardView | null>(null);
  const [agents, setAgents] = useState<Awaited<ReturnType<typeof listAgents>>>([]);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [requestedDecision, setRequestedDecision] = useState<string | null>(null);
  const updating = useRef(false);
  const onUpdateRef = useRef(onUpdate); onUpdateRef.current = onUpdate;
  const id = useId();
  useEffect(() => {
    let cancelled = false;
    const refresh = () => fetchSharedWork(conversationId).then(next => {
      if (!cancelled) { setBoard(prev => prev && prev.revision >= next.revision ? prev : next); setLoadError(''); }
    }).catch(e => { if (!cancelled) setLoadError(e instanceof Error ? e.message : '任务信息暂时无法同步'); });
    void refresh();
    void listAgents().then(value => { if (!cancelled) setAgents(value); }).catch(() => {});
    const timer = window.setInterval(refresh, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [conversationId, reload]);
  useEffect(() => { if (board) onUpdateRef.current?.(); }, [board?.revision]);
  async function command(action: string, data: Record<string, unknown> = {}) {
    if (!board || updating.current) return false;
    updating.current = true; setBusy(true); setError('');
    try {
      const next = await updateSharedWork(conversationId, action, board.revision, data);
      setBoard(prev => prev && prev.revision > next.revision ? prev : next);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : '更新失败，请重试');
      try { const next = await fetchSharedWork(conversationId); setBoard(prev => prev && prev.revision > next.revision ? prev : next); } catch { /* Keep the saved view and feedback on a transient outage. */ }
      return false;
    } finally { updating.current = false; setBusy(false); }
  }
  const pending = board ? pendingDecisions(board) : [];
  const root = board?.tasks.find(t => !t.parent_id && !['done', 'cancelled'].includes(t.status)) ?? board?.tasks.find(t => !t.parent_id);
  const label = (agent: string) => agents.find(a => a.id === agent)?.display_name ?? agent;
  const activate = (next: 'chat' | 'work') => { setTab(next); };
  const returnToChat = () => { setTab('chat'); requestAnimationFrame(() => document.getElementById('mission-input')?.focus()); };
  const openWork = () => { setTab('work'); setRequestedDecision(pending[0]?.id ?? null); };
  const tabs = [{ value: 'chat', text: '对话' }, { value: 'work', text: '任务' }] as const;
  return <div className="work-workspace">
    <div className="work-tabbar" role="tablist" aria-label="会话视图">{tabs.map((item, index) => <button key={item.value} id={`${id}-${item.value}`} role="tab" aria-selected={tab === item.value} aria-controls={`${id}-${item.value}-panel`} tabIndex={tab === item.value ? 0 : -1}
      onClick={() => activate(item.value)} onKeyDown={e => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
        e.preventDefault();
        const next = e.key === 'Home' ? 0 : e.key === 'End' ? 1 : (index + 1) % 2;
        activate(tabs[next].value); document.getElementById(`${id}-${tabs[next].value}`)?.focus();
      }}>{item.text}{item.value === 'work' && pending.length > 0 && <span className="work-tab-count" aria-label={`${pending.length} 项待决定`}>{pending.length}</span>}</button>)}</div>
    <section id={`${id}-chat-panel`} role="tabpanel" aria-labelledby={`${id}-chat`} hidden={tab !== 'chat'} className="work-chat-panel">
      {(root || loadError) && <div className={`work-chat-status${pending.length ? ' has-pending' : ''}`}>
        {pending.length ? <Clock3 size={18} /> : root?.blocker || loadError ? <AlertCircle size={18} /> : root?.status === 'done' ? <CheckCircle2 size={18} /> : <Clock3 size={18} />}
        <div><strong>{loadError ? '任务状态同步中断' : pending.length ? `${pending.length} 项待你处理` : root ? taskStatus(root, board!) : '任务'}</strong>
          <span>{loadError || (root && taskSummary(root, board!, label))}</span></div>
        <button onClick={loadError ? () => setReload(v => v + 1) : openWork}>{loadError ? '重新同步' : pending.length ? '查看并决定' : '查看任务'}<ArrowRight size={15} /></button>
      </div>}
      {chat}<div className="work-conversation-controls">
        {(board?.proposals ?? []).filter(p => p.status === 'pending').map(p => <WorkProposalCard key={p.id} proposal={p} label={label} busy={busy} archived={archived} error={error} command={command} />)}
        {composer}
      </div>
    </section>
    <section id={`${id}-work-panel`} role="tabpanel" aria-labelledby={`${id}-work`} hidden={tab !== 'work'} className="work-task-panel" tabIndex={0}>
      {loadError && <div className="work-load-error" role="status">{loadError}<button onClick={() => setReload(v => v + 1)}><RefreshCw size={15} />重新同步</button></div>}
      {!board ? <p className="work-loading" role="status">正在读取任务…</p> : !board.tasks.length ? <div className="work-empty-state"><h2>先聊聊你想做什么</h2><p>在对话里 @ 一位伙伴，聊清目标和范围。确认任务提议后，这里会展示负责人、进度和关键决定。</p><button className="work-primary" onClick={returnToChat}>回到对话 <ArrowRight size={16} /></button></div> : board.protocol === 'adaptive_v1' ? <AdaptiveWorkBody board={board} conversationId={conversationId} goal={goal} agents={agents} busy={busy} error={error} archived={archived} command={command} onDiscuss={returnToChat} requestedDecision={requestedDecision} onDecisionClosed={() => setRequestedDecision(null)} /> : <SharedWorkBoard conversationId={conversationId} goal={goal} archived={archived} onUpdate={onUpdate} defaultOpen />}
    </section>
  </div>;
}
