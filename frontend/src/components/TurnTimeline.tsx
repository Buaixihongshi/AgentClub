import { Fragment, useLayoutEffect, useRef, type ReactNode } from "react";
import type { A2AInvocationRecord, TurnView } from "../types";
import { ChatBubble } from "./ChatBubble";
import { StatusBadge } from "./StatusBadge";
import { ArtifactPanel } from "./ArtifactPanel";

const AGENT_NAMES: Record<string, string> = {
  chulan: "张楚岚",
  baobao: "冯宝宝",
  wangye: "王也",
};

function A2AArrow({ invocation }: { invocation: A2AInvocationRecord }) {
  const sender = AGENT_NAMES[invocation.sender_agent_id] ?? invocation.sender_agent_id;
  const target = AGENT_NAMES[invocation.target_agent_id] ?? invocation.target_agent_id;
  const isWaitReply = invocation.mode === "wait_reply";
  const arrow = isWaitReply ? "↳⇌" : "↳";
  return (
    <div className={`a2a-arrow${invocation.reply_failed ? " a2a-reply-failed" : ""}`} title={invocation.purpose}>
      <span>{arrow}</span>
      <strong>{sender} {isWaitReply ? "⇌" : "→"} {target}</strong>
      <span>{invocation.purpose}</span>
      {isWaitReply && invocation.reply_text && (
        <div className="a2a-reply-preview" title={invocation.reply_text}>
          ↩ {invocation.reply_text.slice(0, 100)}{invocation.reply_text.length > 100 ? "…" : ""}
        </div>
      )}
    </div>
  );
}

export function TurnTimeline({
  turns,
  conversationId,
  emptyContent,
  afterContent,
  collapsedAgentMissionId,
}: {
  turns: TurnView[];
  conversationId?: string;
  emptyContent?: ReactNode;
  afterContent?: ReactNode;
  collapsedAgentMissionId?: string;
}) {
  const containerRef = useRef<HTMLElement>(null);
  const lastConvRef = useRef<string | undefined>(undefined);
  const lastTurnsLenRef = useRef<number>(0);
  const stickToBottomRef = useRef<boolean>(true);
  const collapsed = collapsedAgentMissionId ? turns.filter((t) => t.missionId === collapsedAgentMissionId && (t.role === 'agent' || t.role === 'assistant')) : [];
  const collapsedIds = new Set(collapsed.map((turn) => turn.id));

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const convChanged = lastConvRef.current !== conversationId;
    if (convChanged) {
      el.scrollTop = el.scrollHeight;
      lastConvRef.current = conversationId;
      lastTurnsLenRef.current = turns.length;
      stickToBottomRef.current = true;
      return;
    }

    if (turns.length > lastTurnsLenRef.current && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
    lastTurnsLenRef.current = turns.length;
  }, [conversationId, turns.length]);

  function handleScroll() {
    const el = containerRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distance < 80;
  }

  if (turns.length === 0) {
    if (emptyContent) {
      return (
        <section className="turn-timeline onboarding-timeline" aria-label="任务轮次">
          {emptyContent}
        </section>
      );
    }
    return (
      <div className="empty-state">创建任务后，Agent 轮次会显示在这里。</div>
    );
  }

  return (
    <section
      ref={containerRef}
      onScroll={handleScroll}
      className="turn-timeline"
      aria-label="任务轮次"
    >
      {turns.filter((turn) => !collapsedIds.has(turn.id)).map((turn) => (
        <Fragment key={turn.id}>
          {turn.a2aInvocation ? <A2AArrow invocation={turn.a2aInvocation} /> : null}
          {turn.artifact ? (
            <ArtifactPanel key={turn.artifact.created_at} missionId={turn.artifact.mission_id} artifacts={[turn.artifact]} variant="chat" />
          ) : turn.isStatusEvent ? <StatusBadge turn={turn} /> : <ChatBubble turn={turn} />}
        </Fragment>
      ))}
      {collapsed.length > 0 && <details className="deliberation-original-messages">
        <summary>查看本场原始发言（{collapsed.length} 条）</summary>
        {collapsed.map((turn) => <ChatBubble key={turn.id} turn={turn} />)}
      </details>}
      {afterContent}
    </section>
  );
}
