import { useCallback, useEffect, useState } from "react";
import {
  clearMemoryCheckpoints,
  fetchAgentMemory,
  fetchMemoryCheckpoints,
  fetchMemoryHandovers,
  fetchMemoryOverview,
  purgeMemoryEntry,
  rebuildMemoryIndex,
  setMemoryEntryStatus,
  updateMemoryEntry,
} from "../lib/api";
import type {
  MemoryCheckpointView,
  MemoryEntryView,
  MemoryHandoverView,
  MemoryOverviewView,
} from "../types";

interface Props {
  conversationId?: string | null;
}

export function MemoryPanel({ conversationId }: Props) {
  const [overview, setOverview] = useState<MemoryOverviewView | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [showForgotten, setShowForgotten] = useState(false);
  const [entries, setEntries] = useState<MemoryEntryView[]>([]);
  const [checkpoints, setCheckpoints] = useState<MemoryCheckpointView[]>([]);
  const [handovers, setHandovers] = useState<MemoryHandoverView[]>([]);
  const [editing, setEditing] = useState<MemoryEntryView | null>(null);
  const [draftBody, setDraftBody] = useState("");
  const [draftTitle, setDraftTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const view = await fetchMemoryOverview();
        if (cancelled) return;
        setOverview(view);
        setSelectedAgent((current) => current ?? view.agents[0]?.id ?? null);
        const switches = await fetchMemoryHandovers();
        if (!cancelled) setHandovers(switches.handovers);
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : "加载记忆失败");
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!selectedAgent) return;
    let cancelled = false;
    setEntries([]);
    fetchAgentMemory(selectedAgent, showForgotten)
      .then((result) => { if (!cancelled) setEntries(result.entries); })
      .catch((exc) => { if (!cancelled) setError(exc instanceof Error ? exc.message : "加载记忆条目失败"); });
    return () => { cancelled = true; };
  }, [selectedAgent, showForgotten]);

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    fetchMemoryCheckpoints(conversationId)
      .then((result) => { if (!cancelled) setCheckpoints(result.checkpoints); })
      .catch(() => { if (!cancelled) setCheckpoints([]); });
    return () => { cancelled = true; };
  }, [conversationId]);

  const refreshOverview = useCallback(async () => {
    try {
      setOverview(await fetchMemoryOverview());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "刷新记忆状态失败");
    }
  }, []);

  const run = useCallback(async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      if (selectedAgent) setEntries((await fetchAgentMemory(selectedAgent, showForgotten)).entries);
      await refreshOverview();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }, [selectedAgent, showForgotten, refreshOverview]);

  const startEdit = (entry: MemoryEntryView) => {
    setEditing(entry);
    setDraftTitle(entry.title);
    setDraftBody(entry.body);
  };

  const consistency = overview?.consistency;
  const drift = consistency ? consistency.stale + consistency.missing_files : 0;

  return (
    <div className="memory-layout" aria-label="记忆管理">
      <div className="settings-section">
        <h3>记忆</h3>
        <p className="settings-section-hint">长期记忆以 Markdown 文件为准；忘记仅移出上下文，清除会删除文件。</p>
      </div>
      {error ? <div role="alert" className="error-banner">{error}</div> : null}
      <div className="memory-columns">
          <nav className="memory-agent-list" aria-label="角色记忆">
            {(overview?.agents ?? []).map((agent) => (
              <button
                key={agent.id}
                type="button"
                className={agent.id === selectedAgent ? "memory-agent active" : "memory-agent"}
                onClick={() => { setSelectedAgent(agent.id); setEditing(null); }}
              >
                <span className="memory-agent-name">{agent.display_name}</span>
                <span className="memory-agent-count">{agent.active} 条{agent.forgotten ? ` · 已忘记 ${agent.forgotten}` : ""}</span>
              </button>
            ))}
          </nav>
          <section className="memory-entry-list" aria-label="记忆条目">
            <div className="memory-toolbar">
              <label>
                <input
                  type="checkbox"
                  checked={showForgotten}
                  onChange={(event) => setShowForgotten(event.target.checked)}
                />
                显示已忘记
              </label>
              <button
                type="button"
                className="memory-rebuild"
                disabled={busy}
                onClick={() => run(async () => { await rebuildMemoryIndex(); })}
              >
                重建索引
              </button>
            </div>
            {editing ? (
              <div className="memory-editor">
                <label>
                  标题
                  <input value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} aria-label="记忆标题" />
                </label>
                <label>
                  正文
                  <textarea value={draftBody} rows={10} onChange={(event) => setDraftBody(event.target.value)} aria-label="记忆正文" />
                </label>
                <div className="memory-editor-actions">
                  <button
                    type="button"
                    disabled={busy || !draftTitle.trim() || !draftBody.trim()}
                    onClick={() => run(async () => {
                      await updateMemoryEntry(editing.id, { title: draftTitle, body: draftBody });
                      setEditing(null);
                    })}
                  >
                    保存修订
                  </button>
                  <button type="button" onClick={() => setEditing(null)}>取消</button>
                </div>
              </div>
            ) : null}
            {entries.length === 0 && !editing ? (
              <p className="memory-empty">该角色暂无{showForgotten ? "" : "有效"}记忆条目。</p>
            ) : null}
            {entries.map((entry) => (
              <article key={entry.id} className={entry.status === 'forgotten' ? 'memory-entry forgotten' : 'memory-entry'}>
                <header className="memory-entry-header">
                  <h3>{entry.title}</h3>
                  <span className="memory-entry-meta">r{entry.revision} · {entry.updated_at.slice(0, 10)}{entry.tags.length ? ` · ${entry.tags.join(' #')}` : ''}</span>
                </header>
                <p className="memory-entry-body">{entry.body}</p>
                <div className="memory-entry-actions">
                  <button type="button" disabled={busy} onClick={() => startEdit(entry)}>修订</button>
                  {entry.status === 'forgotten' ? (
                    <button type="button" disabled={busy} onClick={() => run(() => setMemoryEntryStatus(entry.id, 'active'))}>恢复</button>
                  ) : (
                    <button type="button" disabled={busy} onClick={() => run(() => setMemoryEntryStatus(entry.id, 'forgotten'))}>忘记</button>
                  )}
                  <button type="button" className="memory-danger" disabled={busy} onClick={() => run(() => purgeMemoryEntry(entry.id))}>清除</button>
                </div>
              </article>
            ))}
          </section>
          <aside className="memory-side">
            {conversationId ? (
              <section aria-label="会话检查点">
                <h3>本会话检查点</h3>
                {checkpoints.length === 0 ? <p className="memory-empty">暂无检查点。</p> : checkpoints.map((checkpoint) => (
                  <div key={checkpoint.agent_id} className="memory-checkpoint">
                    <strong>{checkpoint.agent_id}</strong>
                    <p>{checkpoint.text}</p>
                    <button
                      type="button"
                      disabled={busy}
                      aria-label={`清除 ${checkpoint.agent_id} 的检查点`}
                      onClick={() => run(async () => {
                        await clearMemoryCheckpoints(conversationId, checkpoint.agent_id);
                        setCheckpoints((await fetchMemoryCheckpoints(conversationId)).checkpoints);
                      })}
                    >清除</button>
                  </div>
                ))}
                {checkpoints.length > 0 ? (
                  <button type="button" disabled={busy} onClick={() => run(async () => {
                    await clearMemoryCheckpoints(conversationId);
                    setCheckpoints((await fetchMemoryCheckpoints(conversationId)).checkpoints);
                  })}>清除本会话全部检查点</button>
                ) : null}
              </section>
            ) : null}
            <section aria-label="一致性状态">
              <h3>存储一致性</h3>
              {consistency ? (
                <p className={drift ? 'memory-drift' : 'memory-ok'}>
                  文件 {consistency.files} · 索引 {consistency.indexed}
                  {drift ? ` · 漂移 ${drift}（重建索引可修正）` : ' · 一致'}
                </p>
              ) : <p className="memory-empty">加载中…</p>}
              <p className="memory-path">{overview?.memory_root}</p>
            </section>
            <section aria-label="模型交接">
              <h3>最近模型交接</h3>
              {handovers.length === 0 ? <p className="memory-empty">暂无记录。</p> : handovers.slice(0, 5).map((handover) => (
                <p key={handover.id} className="memory-handover">
                  {handover.agent_id}：{handover.previous || '未知'} → {handover.current}
                  （{handover.reason === 'settings' ? '设置变更' : handover.reason === 'fallback' ? '执行回退' : handover.reason}{handover.acknowledged ? '' : '，待交接'}）
                </p>
              ))}
            </section>
          </aside>
        </div>
      </div>
  );
}
