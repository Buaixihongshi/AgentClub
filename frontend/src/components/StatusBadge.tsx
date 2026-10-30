import type { HostProgress, TurnView } from "../types";
import { NativeActivity } from "./NativeActivity";

const TIMEOUT_REASONS: Record<string, string> = {
  hard_timeout: "单轮总时长用尽",
  idle_timeout: "接力回合长时间没有新活动",
  provider_timeout: "CLI 自身报告超时",
  timeout: "执行超时",
};

function HostProgressNote({ progress }: { progress: HostProgress }) {
  const tools = progress.tools.map((tool) => `${tool.name} ×${tool.count}`).join("、");
  const last = progress.last_activity;
  const lastTime = last?.at ? new Date(last.at).toLocaleTimeString() : "";
  return (
    <section className="host-progress" aria-label="宿主进度记录">
      <h4>宿主记录 · 非角色发言</h4>
      <dl>
        <dt>阻塞原因</dt>
        <dd>{TIMEOUT_REASONS[progress.reason] ?? progress.reason}</dd>
        <dt>已调用工具</dt>
        <dd>{progress.tool_calls ? `${progress.tool_calls} 次：${tools}` : "无"}</dd>
        <dt>已登记产物</dt>
        <dd>{progress.artifacts.length ? progress.artifacts.join("、") : "无"}</dd>
        <dt>最后活动</dt>
        <dd>
          {last
            ? [last.label, lastTime, last.seconds_before_stop != null ? `停止前 ${last.seconds_before_stop} 秒` : ""]
                .filter(Boolean).join(" · ")
            : "未记录到活动"}
        </dd>
      </dl>
    </section>
  );
}

export function StatusBadge({ turn }: { turn: TurnView }) {
  if (turn.eventType === "turn.failure" || turn.eventType === "mission.failure") {
    const progress = turn.metadata?.host_progress;
    return (
      <article className="turn-failure" aria-label={`状态：${turn.content}`}>
        <p>{turn.content}</p>
        {progress?.source === "host" && <HostProgressNote progress={progress} />}
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
