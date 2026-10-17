import type { TurnView } from "../types";
import { NativeActivity } from "./NativeActivity";

export function StatusBadge({ turn }: { turn: TurnView }) {
  if (turn.eventType === "turn.failure" || turn.eventType === "mission.failure") {
    return (
      <article className="turn-failure" aria-label={`状态：${turn.content}`}>
        <p>{turn.content}</p>
        <NativeActivity calls={turn.toolCalls} />
      </article>
    );
  }
  return (
    <div className="status-badge" aria-label={`状态：${turn.content}`}>
      <span className="status-badge-dot" />
      <span className="status-badge-text">{turn.content}</span>
    </div>
  );
}
