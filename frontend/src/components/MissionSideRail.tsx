import type { ProviderStatusState } from "../hooks/useProviderStatus";
import { buildProviderIssueSummary, deriveTurnPath, formatMissionStatus } from "../lib/missionView";
import type {
  A2AInvocationRecord,
  InterruptRecord,
  MissionDetail,
  ToolEventRecord,
  TurnAttempt,
  TurnRecord,
  OnboardingView,
} from "../types";
import { ArtifactPanel } from "./ArtifactPanel";
import { OnboardingProgress } from "./OnboardingProgress";
import { ProviderStatusPanel } from "./ProviderStatusPanel";

export function MissionSideRail({
  detail,
  providerStatus,
  onboarding,
}: {
  detail?: MissionDetail;
  providerStatus: ProviderStatusState;
  onboarding?: OnboardingView | null;
}) {
  const mission = detail?.mission;
  const artifacts = detail?.artifacts ?? [];
  const turns = detail?.turns;
  const toolEvents = detail?.tool_events;
  const interrupts = detail?.interrupts;
  const a2aInvocations = detail?.a2a_invocations;
  const providerIssues = buildProviderIssueSummary(providerStatus.providers);

  return (
    <aside className="mission-side-rail" aria-label="任务侧栏">
      {onboarding && onboarding.state !== "completed" ? (
        <OnboardingProgress onboarding={onboarding} />
      ) : null}
      <section className="side-panel" aria-label="任务状态">
        <h2>任务状态</h2>
        <p className="side-panel-primary">{formatMissionStatus(mission?.status)}</p>
        <p>复杂度：{mission?.complexity ?? "unknown"}</p>
        {mission?.created_at ? <p>创建：{new Date(mission.created_at).toLocaleString("zh-CN")}</p> : null}
        {mission?.updated_at ? <p>更新：{new Date(mission.updated_at).toLocaleString("zh-CN")}</p> : null}
        {mission?.completed_at ? <p>完成：{new Date(mission.completed_at).toLocaleString("zh-CN")}</p> : null}
      </section>

      <ProviderStatusPanel {...providerStatus} onboarding={onboarding} />

      {turns !== undefined ? <TurnProgressPanel turns={turns} /> : null}

      {toolEvents !== undefined ? <ToolTracePanel toolEvents={toolEvents} /> : null}

      {interrupts !== undefined ? <InterruptQueuePanel interrupts={interrupts} /> : null}

      {a2aInvocations !== undefined ? (
        <A2ABudgetPanel invocations={a2aInvocations} maxInvocations={8} />
      ) : null}

      <ArtifactPanel missionId={mission?.id} artifacts={artifacts} />

      <section className="side-panel" aria-label="阻塞和错误">
        <h2>阻塞 / 错误</h2>
        {mission?.blocked_reason ? <p>{mission.blocked_reason}</p> : null}
        {providerIssues.length > 0 ? (
          <ul className="issue-list">
            {providerIssues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        ) : !mission?.blocked_reason ? (
          <p>暂无阻塞</p>
        ) : null}
      </section>
    </aside>
  );
}

function TurnPathBadge({ turn }: { turn: TurnRecord }) {
  const path = deriveTurnPath(turn);
  if (path === "mcp") {
    return <span className="turn-path-badge turn-path-badge--mcp">MCP</span>;
  }
  if (path === "stdout") {
    return <span className="turn-path-badge turn-path-badge--stdout">stdout</span>;
  }
  return <span className="turn-path-badge turn-path-badge--fake">演示</span>;
}

function TurnProgressPanel({ turns }: { turns: TurnRecord[] }) {
  const latestTurn = turns.length > 0 ? turns[turns.length - 1] : undefined;
  return (
    <section className="side-panel" aria-label="轮次追踪">
      <h2>轮次追踪</h2>
      {latestTurn ? (
        <div className="runtime-panel-body">
          <p className="side-panel-primary">
            {latestTurn.turn_id}
            {" "}
            <TurnPathBadge turn={latestTurn} />
          </p>
          <dl className="side-key-value-list">
            <div>
              <dt>Agent</dt>
              <dd>Agent：{latestTurn.agent_id}</dd>
            </div>
            <div>
              <dt>Provider</dt>
              <dd>Provider：{formatProviderId(latestTurn.provider_id)}</dd>
            </div>
            <div>
              <dt>状态</dt>
              <dd>状态：{formatTurnStatus(latestTurn.status)}</dd>
            </div>
            <div>
              <dt>尝试</dt>
              <dd>尝试：{latestTurn.attempts.length}</dd>
            </div>
            {latestTurn.duration_ms !== null && latestTurn.duration_ms !== undefined ? (
              <div>
                <dt>耗时</dt>
                <dd>耗时：{latestTurn.duration_ms}ms</dd>
              </div>
            ) : null}
          </dl>
        </div>
      ) : (
        <p>暂无轮次追踪</p>
      )}
      <ProviderFallbackList turns={turns} />
    </section>
  );
}

function ProviderFallbackList({ turns }: { turns: TurnRecord[] }) {
  const fallbackTurns = turns.filter(hasProviderFallback);
  if (fallbackTurns.length === 0) {
    return null;
  }
  return (
    <div className="provider-fallback-panel" aria-label="Provider fallback 尝试链">
      <strong>默认 Provider 失败，已切换备用 Provider</strong>
      <ul>
        {fallbackTurns.map((turn) => (
          <li key={turn.turn_id}>
            <span>{turn.turn_id}</span>
            <ProviderAttemptList attempts={turn.attempts} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function ProviderAttemptList({ attempts }: { attempts: TurnAttempt[] }) {
  return (
    <ol>
      {attempts.map((attempt) => {
        const status = normalizeAttemptStatus(attempt);
        const detail = attempt.error ?? attempt.summary;
        return (
          <li key={`${attempt.provider_id}-${attempt.attempt}-${attempt.event_type ?? status}`}>
            <span>{formatProviderId(attempt.provider_id)}：{formatAttemptStatus(status)}</span>
            {detail ? <small>{detail}</small> : null}
          </li>
        );
      })}
    </ol>
  );
}

function hasProviderFallback(turn: TurnRecord): boolean {
  if (turn.attempts.length < 2) {
    return false;
  }
  const providerIds = new Set(turn.attempts.map((attempt) => attempt.provider_id));
  const statuses = turn.attempts.map(normalizeAttemptStatus);
  return providerIds.size > 1 && statuses.some((status) => status === "blocked" || status === "failed");
}

function ToolTracePanel({ toolEvents }: { toolEvents: ToolEventRecord[] }) {
  if (toolEvents.length === 0) {
    return (
      <section className="side-panel" aria-label="工具追踪">
        <h2>工具追踪</h2>
        <p>暂无工具调用</p>
      </section>
    );
  }

  return (
    <section className="side-panel tool-trace-panel" aria-label="工具追踪">
      <details className="tool-trace-disclosure">
        <summary>
          <span className="tool-trace-title">工具追踪</span>
          <span className="tool-trace-count">{toolEvents.length} 条</span>
        </summary>
        <ul className="runtime-list">
          {toolEvents.map((event) => (
            <li key={String(event.id)}>
              <strong>{event.tool_name}</strong>
              <span>{event.event_type} · {event.agent_id} · {event.turn_id}</span>
              {event.summary ? <span>{event.summary}</span> : null}
              {event.error ? <span className="runtime-error">{event.error}</span> : null}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

function InterruptQueuePanel({ interrupts }: { interrupts: InterruptRecord[] }) {
  const sortedInterrupts = [...interrupts].sort((a, b) => {
    if (a.status !== b.status) return a.status === "pending" ? -1 : 1;
    return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
  });
  return (
    <section className="side-panel" aria-label="插话队列">
      <h2>插话队列</h2>
      {sortedInterrupts.length === 0 ? (
        <p>暂无插话</p>
      ) : (
        <ul className="runtime-list">
          {sortedInterrupts.map((interrupt) => (
            <li key={String(interrupt.event_id)}>
              <strong>{formatInterruptStatus(interrupt.status)}</strong>
              <span>{interrupt.content}</span>
              <span>
                目标：{interrupt.target_agent_id ?? "未指定"}
                {interrupt.handled_by_agent_id ? ` · 处理：${interrupt.handled_by_agent_id}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function A2ABudgetPanel({
  invocations,
  maxInvocations,
}: {
  invocations: A2AInvocationRecord[];
  maxInvocations: number;
}) {
  const used = invocations.length;
  const hot = used / maxInvocations >= 0.7;
  const pendingWaitCount = invocations.filter((i) => i.mode === "wait_reply" && !i.reply_text).length;
  if (invocations.length === 0) {
    return (
      <section className={`side-panel ${hot ? "side-panel-warning" : ""}`} aria-label="A2A 路由">
        <h2>A2A 路由</h2>
        <p className="side-panel-primary">{used} / {maxInvocations}</p>
        <p>暂无 Agent 接力</p>
      </section>
    );
  }

  return (
    <section className={`side-panel a2a-route-panel ${hot ? "side-panel-warning" : ""}`} aria-label="A2A 路由">
      <details className="a2a-route-disclosure">
        <summary>
          <span className="a2a-route-title">A2A 路由</span>
          <span className="a2a-route-count">{used} / {maxInvocations}</span>
        </summary>
        {pendingWaitCount > 0 && (
          <p className="a2a-pending-wait">
            等待 {pendingWaitCount} 条 wait_reply 回复
          </p>
        )}
        <ul className="runtime-list">
          {invocations.map((item) => {
            const arrow = item.mode === "wait_reply" ? "⇌" : "→";
            return (
              <li key={String(item.event_id)} className={item.reply_failed ? "a2a-reply-failed" : ""}>
                <strong>{item.sender_agent_id} {arrow} {item.target_agent_id}</strong>
                <span>{item.purpose}</span>
                {item.mode === "wait_reply" && item.reply_text && (
                  <div className="a2a-reply-preview" title={item.reply_text}>
                    ↩ {item.reply_text.slice(0, 80)}{item.reply_text.length > 80 ? "…" : ""}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </details>
    </section>
  );
}

function formatTurnStatus(status: TurnRecord["status"]): string {
  const labels: Record<TurnRecord["status"], string> = {
    started: "进行中",
    completed: "已完成",
    blocked: "已阻塞",
    failed: "失败",
  };
  return labels[status];
}

function normalizeAttemptStatus(attempt: TurnAttempt): TurnRecord["status"] {
  if (attempt.status) {
    return attempt.status;
  }
  if (attempt.event_type?.endsWith(".completed")) {
    return "completed";
  }
  if (attempt.event_type?.endsWith(".blocked")) {
    return "blocked";
  }
  if (attempt.event_type?.endsWith(".failed")) {
    return "failed";
  }
  return "started";
}

function formatAttemptStatus(status: TurnRecord["status"]): string {
  const labels: Record<TurnRecord["status"], string> = {
    started: "进行中",
    completed: "已完成",
    blocked: "已阻塞",
    failed: "失败",
  };
  return labels[status];
}

function formatProviderId(providerId: string): string {
  const labels: Record<string, string> = {
    claude: "Claude",
    codex: "Codex",
    gemini: "Gemini",
    antigravity: "Antigravity",
    fake: "演示",
  };
  return labels[providerId] ?? providerId;
}

function formatInterruptStatus(status: InterruptRecord["status"]): string {
  return status === "pending" ? "待处理" : "已处理";
}
