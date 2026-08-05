import type { TurnView } from "../types";

export function StatusBadge({ turn }: { turn: TurnView }) {
  return (
    <div className="status-badge" aria-label={`状态：${turn.content}`}>
      <span className="status-badge-dot" />
      <span className="status-badge-text">{turn.content}</span>
    </div>
  );
}
