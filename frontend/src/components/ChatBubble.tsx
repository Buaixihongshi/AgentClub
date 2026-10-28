import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { getPersonaColor } from "../lib/personaColors";
import { compactPersonaProviderLabel } from "../lib/providerLabels";
import type { TurnView } from "../types";
import { ThoughtBlock } from "./ThoughtBlock";
import { TurnMetadataDisclosure } from "./TurnMetadataDisclosure";
import { liveActivityLabel } from "./NativeActivity";

function MessageMarkdown({ content }: { content: string }) {
  return (
    <div className="bubble-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export function ChatBubble({ turn }: { turn: TurnView }) {
  if (turn.role === "user") {
    return (
      <article className="chat-bubble chat-bubble-user">
        <div className="bubble-sender">老板</div>
        <MessageMarkdown content={turn.content} />
      </article>
    );
  }

  const color = getPersonaColor(turn.agentId);
  const identity = compactPersonaProviderLabel(turn.personaLabel ?? turn.agentId, turn.providerLabel);
  const isStreaming =
    !turn.content && (turn.eventType === "message.partial" || !!turn.partialText);
  const displayContent = turn.content || turn.partialText || "";
  const showStreamingStatus = isStreaming && !displayContent;
  const audit = turn.metadata.discussion_audit;
  const discussionResult = turn.metadata.discussion_visibility === 'final';
  // Native activity is a live progress hint; once the turn finishes it lives in the thought block's tool list.
  const activityLine = isStreaming && !discussionResult ? liveActivityLabel(turn.toolCalls) : null;
  const actions = { accepted: '已采纳', partially_accepted: '部分采纳', not_accepted: '未采纳' };

  return (
    <article
      className="chat-bubble chat-bubble-agent"
      style={{ borderLeftColor: color.border }}
    >
      <div className="bubble-turn-seq">{turn.metadata.discussion_visibility === 'final' ? '团队讨论结果' : `第 ${turn.turnIndex} 轮`}</div>
      <div className="bubble-header">
        <span className="bubble-persona" style={{ color: color.name }}>
          {identity}
        </span>
      </div>
      {!discussionResult && <ThoughtBlock thoughts={turn.thoughts} toolCalls={turn.toolCalls} />}
      {displayContent && <MessageMarkdown content={displayContent} />}
      {turn.metadata.discussion_pending_decision && <p className="discussion-result-note">其中的取舍仍待你决定。</p>}
      {turn.metadata.discussion_revision_reviewed === false && <p className="discussion-result-note">此稿已按复核意见修订，修订部分未再次独立复核。</p>}
      {audit && (audit.sources.length > 0 || audit.reviews.length > 0) && <details className="discussion-result-audit">
        <summary>查看依据与复核记录</summary>
        {audit.sources.length > 0 && <ul aria-label="讨论依据">{audit.sources.map((source) => <li key={source.id}>
          <strong>{source.label}</strong>{source.summary && <p>{source.summary}</p>}
        </li>)}</ul>}
        {audit.reviews.length > 0 && <ul aria-label="复核记录">{audit.reviews.map((review, index) => <li key={`${review.reviewer}:${index}`}>
          <strong>{review.reviewer}</strong><p>{review.problem}</p>
          {review.action && <p>{actions[review.action]}{review.reason ? `：${review.reason}` : ''}</p>}
        </li>)}</ul>}
      </details>}
      {(showStreamingStatus || activityLine) && (
        <div className="streaming-status" aria-live="polite">
          <span className={activityLine ? "streaming-activity" : undefined}>{activityLine ?? "等待完整回复"}</span>
          <span className="typing-dots" aria-hidden="true">
            <span /><span /><span />
          </span>
        </div>
      )}
      {isStreaming && !showStreamingStatus && !activityLine && (
        <span className="typing-dots" aria-hidden="true">
          <span /><span /><span />
        </span>
      )}
      <TurnMetadataDisclosure metadata={turn.metadata} usage={turn.usage} />
    </article>
  );
}
