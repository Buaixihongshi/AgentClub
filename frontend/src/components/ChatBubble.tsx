import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { getPersonaColor } from "../lib/personaColors";
import { compactPersonaProviderLabel } from "../lib/providerLabels";
import type { TurnView } from "../types";
import { ThoughtBlock } from "./ThoughtBlock";
import { TurnMetadataDisclosure } from "./TurnMetadataDisclosure";

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

  return (
    <article
      className="chat-bubble chat-bubble-agent"
      style={{ borderLeftColor: color.border }}
    >
      <div className="bubble-turn-seq">第 {turn.turnIndex} 轮</div>
      <div className="bubble-header">
        <span className="bubble-persona" style={{ color: color.name }}>
          {identity}
        </span>
      </div>
      <ThoughtBlock thoughts={turn.thoughts} toolCalls={turn.toolCalls} />
      {displayContent && <MessageMarkdown content={displayContent} />}
      {showStreamingStatus && (
        <div className="streaming-status" aria-live="polite">
          <span>等待完整回复</span>
          <span className="typing-dots" aria-hidden="true">
            <span /><span /><span />
          </span>
        </div>
      )}
      {isStreaming && !showStreamingStatus && (
        <span className="typing-dots" aria-hidden="true">
          <span /><span /><span />
        </span>
      )}
      <TurnMetadataDisclosure metadata={turn.metadata} usage={turn.usage} />
    </article>
  );
}
