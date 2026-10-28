import { useEffect, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, ArrowRight, CheckCircle2, Clock3 } from 'lucide-react';
import { fetchSharedWork, listAgents, updateSharedWork } from '../lib/api';
import type { SharedWorkBoardView } from '../types';
import { pendingDecisions, taskStatus, taskSummary } from '../lib/workPresentation';
import { WorkProposalCard } from './WorkProposalCard';
import './WorkWorkspace.css';

/** Conversation intake stays here; durable work is managed in the global task workspace. */
export function SharedWorkWorkspace({ conversationId, archived, onUpdate, chat, composer, onOpenTask }: {
  conversationId: string; goal: string; archived: boolean; onUpdate?: () => void;
  chat: ReactNode; composer: ReactNode; onOpenTask?: (id: string) => void;
}) {
  const [board, setBoard] = useState<SharedWorkBoardView | null>(null);
  const [agents, setAgents] = useState<Awaited<ReturnType<typeof listAgents>>>([]);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const updating = useRef(false);
  const onUpdateRef = useRef(onUpdate); onUpdateRef.current = onUpdate;
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
      try { const next = await fetchSharedWork(conversationId); setBoard(prev => prev && prev.revision > next.revision ? prev : next); } catch { /* Preserve proposal feedback during a transient outage. */ }
      return false;
    } finally { updating.current = false; setBusy(false); }
  }
  const pending = board ? pendingDecisions(board) : [];
  const attentionTask = board?.tasks.find(t => pending.some(d => d.task_id === t.id));
  const root = board?.tasks.find(t => !t.parent_id && t.id === (attentionTask?.root_id ?? attentionTask?.id))
    ?? board?.tasks.find(t => !t.parent_id && !['done', 'cancelled'].includes(t.status))
    ?? board?.tasks.find(t => !t.parent_id);
  const label = (agent: string) => agents.find(a => a.id === agent)?.display_name ?? agent;
  const openWork = () => {
    if (!root) return;
    if (onOpenTask) onOpenTask(root.id);
    else window.location.hash = `tasks/${encodeURIComponent(root.id)}`;
  };
  return <div className="work-workspace conversation-workspace">
    <section className="work-chat-panel" aria-label="会话内容">
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
  </div>;
}
