import type { TurnToolCall } from "../types";
import "./NativeActivity.css";

function phaseLabel(call: TurnToolCall, live: boolean): string {
  if (call.phase === "completed") return "已完成";
  if (call.phase === "failed") return "未完成";
  return live ? "进行中" : "未收到完成确认";
}

export function NativeActivity({ calls = [], live = false }: { calls?: TurnToolCall[]; live?: boolean }) {
  const activities = calls.filter((call) => call.eventType === "provider.activity");
  if (!activities.length) return null;
  const latest = activities[activities.length - 1];
  return (
    <details className="native-activity">
      <summary>
        最近活动：{latest.summary ?? latest.toolName}（{phaseLabel(latest, live)}）
        <span className="native-activity-count"> · {activities.length} 项</span>
      </summary>
      <ol aria-label="原生活动记录">
        {activities.map((call) => (
          <li key={call.activityId}>
            {call.summary ?? call.toolName}
            <span className="native-activity-phase">{phaseLabel(call, live)}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}
