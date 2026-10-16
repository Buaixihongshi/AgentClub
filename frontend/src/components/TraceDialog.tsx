import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, X } from "lucide-react";
import { formatAgentName, formatMissionStatus } from "../lib/missionView";
import type { TraceTool, TraceTurn, TraceView } from "../lib/traceView";
import type { A2AInvocationRecord, MissionDetail } from "../types";
import { ArtifactPanel } from "./ArtifactPanel";

const LANES = ["chulan", "baobao", "wangye"];
const PROVIDERS: Record<string, string> = {
  claude: "Claude",
  codex: "Codex",
  antigravity: "Antigravity",
  fake: "Fake",
};

type Selection = { type: "tool"; id: string } | { type: "route"; id: string } | null;

export function TraceDialog({ detail, trace, onClose }: {
  detail?: MissionDetail;
  trace: TraceView;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const selectedTool = trace.turns.flatMap((turn) => turn.tools).find(
    (tool) => selection?.type === "tool" && tool.id === selection.id,
  );
  const selectedRoute = trace.invocations.find(
    (route) => selection?.type === "route" && String(route.event_id) === selection.id,
  );
  const turnNumber = new Map(trace.turns.map((turn) => [turn.id, turn.index]));
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (document.querySelector(".artifact-modal")) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], summary, [tabindex]:not([tabindex="-1"])',
      ));
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  return (
    <div className="trace-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <div ref={dialogRef} className="trace-dialog" role="dialog" aria-modal="true"
        aria-labelledby="trace-dialog-title" tabIndex={-1}>
        <header className="trace-dialog-header">
          <div>
            <p className="trace-eyebrow">MISSION ROOM / LIVE TRACE</p>
            <h2 id="trace-dialog-title">工具与协作追踪</h2>
            <p>按轮次看工具调用，也看清角色之间的委派与回复。</p>
          </div>
          <button className="trace-close" type="button" aria-label="关闭追踪弹窗" onClick={onClose}>
            <X size={22} aria-hidden="true" />
          </button>
        </header>

        <div className="trace-dialog-meta">
          <strong>{formatMissionStatus(detail?.mission.status)}</strong>
          <span>{trace.turns.length} 轮</span>
          <span>{trace.callCount} 次工具调用</span>
          <span>{trace.invocations.length} 次委派</span>
        </div>

        <div className="trace-dialog-scroll">
          <div className="trace-legend" aria-label="图例">
            <span><ArrowRight size={18} /> 实线：委派</span>
            <span className="trace-legend-reply"><ArrowLeft size={18} /> 虚线：回复</span>
            <small>纵向为轮次顺序，不代表耗时</small>
            <small className="trace-swipe-hint">左右滑动查看三位 Agent</small>
          </div>
          <div className="trace-chart" aria-label="协作时序图">
            <div className="trace-chart-heading">
              <span>轮次</span>
              {LANES.map((agent) => (
                <div key={agent} className={`trace-lane-heading trace-agent--${agent}`}>
                  <span className="trace-agent-dot" aria-hidden="true" />
                  <strong>{formatAgentName(agent)}</strong>
                </div>
              ))}
            </div>
            {trace.turns.length === 0 ? (
              <p className="trace-empty">暂无轮次。任务开始后，这里会显示调用与协作关系。</p>
            ) : trace.turns.map((turn) => {
              const outgoing = trace.invocations.filter((route) => route.sender_turn_id === turn.id);
              const incoming = trace.invocations.filter((route) => route.target_turn_id === turn.id);
              return (
                <div className="trace-round" key={turn.id}>
                  {incoming.map((route) => (
                    <TraceArrow key={`out-${route.event_id}`} from={route.sender_agent_id}
                      to={route.target_agent_id} label={route.purpose || "委派任务"}
                      kind="delegate" selected={selectedRoute?.event_id === route.event_id}
                      onClick={() => setSelection({ type: "route", id: String(route.event_id) })} />
                  ))}
                  <div className="trace-turn-row">
                    <div className="trace-round-label">
                      <strong>第 {turn.index} 轮</strong>
                      <small>{turn.status === "started" ? "进行中" : turn.status === "completed" ? "已完成" : "未完成"}</small>
                    </div>
                    {LANES.map((agent) => (
                      <div className="trace-turn-lane" key={agent}>
                        {turn.agentId === agent ? (
                          <TurnCard turn={turn} onSelect={(id) => setSelection({ type: "tool", id })} selectedId={selectedTool?.id} />
                        ) : null}
                      </div>
                    ))}
                  </div>
                  {outgoing.filter((route) => !route.target_turn_id || !trace.turns.some((item) => item.id === route.target_turn_id)).map((route) => (
                    <TraceArrow key={`out-${route.event_id}`} from={route.sender_agent_id}
                      to={route.target_agent_id} label={`等待接力 · ${route.purpose || "委派任务"}`}
                      kind="delegate" selected={selectedRoute?.event_id === route.event_id}
                      onClick={() => setSelection({ type: "route", id: String(route.event_id) })} />
                  ))}
                  {incoming.filter((route) => route.mode !== "fire_and_forget" && (route.reply_text || route.reply_failed)).map((route) => (
                    <TraceArrow key={`reply-${route.event_id}`} from={route.target_agent_id}
                      to={route.sender_agent_id} label={route.reply_failed ? "回复失败" : route.reply_text || "已回复"}
                      kind="reply" selected={selectedRoute?.event_id === route.event_id}
                      onClick={() => setSelection({ type: "route", id: String(route.event_id) })} />
                  ))}
                </div>
              );
            })}
          </div>

          <details className="trace-artifacts">
            <summary>产物 <span>{detail?.artifacts?.length ?? 0}</span></summary>
            <ArtifactPanel missionId={detail?.mission.id} artifacts={detail?.artifacts ?? []} />
          </details>
        </div>
        <section className="trace-inspector" aria-label="追踪详情">
          {selectedTool ? <ToolDetails tool={selectedTool} /> : selectedRoute ? (
            <RouteDetails route={selectedRoute} turnNumber={turnNumber} />
          ) : <p>点击工具节点或协作连线，查看调用参数与请求、回复。</p>}
        </section>
      </div>
    </div>
  );
}

function TurnCard({ turn, onSelect, selectedId }: {
  turn: TraceTurn;
  onSelect: (id: string) => void;
  selectedId?: string;
}) {
  return (
    <div className={`trace-turn-card trace-agent--${turn.agentId}`}>
      <div className="trace-turn-card-header">
        <span className="trace-agent-dot" aria-hidden="true" />
        <strong>{formatAgentName(turn.agentId)}</strong>
        <small>{PROVIDERS[turn.providerId] ?? turn.providerId}</small>
      </div>
      {turn.tools.length ? (
        <ol className="trace-tool-list">
          {turn.tools.map((tool, index) => (
            <li key={tool.id}>
              <button type="button" className={`trace-tool ${selectedId === tool.id ? "trace-tool--selected" : ""}`}
                onClick={() => onSelect(tool.id)} aria-pressed={selectedId === tool.id}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{tool.name}</strong>
                {tool.outcome?.event_type === "tool.error" ? <em>失败</em> : null}
              </button>
            </li>
          ))}
        </ol>
      ) : <p className="trace-no-tools">本轮暂无工具调用</p>}
      {turn.issue ? <p className="trace-turn-issue">{turn.issue}</p> : null}
    </div>
  );
}

function TraceArrow({ from, to, label, kind, selected, onClick }: {
  from: string;
  to: string;
  label: string;
  kind: "delegate" | "reply";
  selected: boolean;
  onClick: () => void;
}) {
  const fromIndex = LANES.indexOf(from);
  const toIndex = LANES.indexOf(to);
  if (fromIndex < 0 || toIndex < 0) return null;
  const left = (Math.min(fromIndex, toIndex) + 0.5) / LANES.length * 100;
  const width = Math.max(Math.abs(toIndex - fromIndex) / LANES.length * 100, 12);
  const pointsLeft = fromIndex > toIndex;
  return (
    <div className="trace-link-row">
      <button type="button"
        className={`trace-link trace-link--${kind} ${selected ? "trace-link--selected" : ""}`}
        style={{ left: `${left}%`, width: `${width}%` }}
        onClick={onClick}
        aria-label={`${formatAgentName(from)}${kind === "delegate" ? "委派给" : "回复"}${formatAgentName(to)}：${label}`}>
        <span className="trace-link-caption" title={label}>{label}</span>
        <span className="trace-link-line" aria-hidden="true">
          {pointsLeft ? <ArrowLeft size={18} /> : <ArrowRight size={18} />}
        </span>
      </button>
    </div>
  );
}

function ToolDetails({ tool }: { tool: TraceTool }) {
  return (
    <>
      <div className="trace-inspector-heading"><strong>{tool.name}</strong><span>工具调用</span></div>
      {tool.input && Object.keys(tool.input).length > 0 ? (
        <pre>{JSON.stringify(tool.input, null, 2)}</pre>
      ) : <p>没有记录调用参数。</p>}
      {tool.outcome?.summary ? <p>{tool.outcome.summary}</p> : null}
      {tool.outcome?.error ? <p className="trace-error">{tool.outcome.error}</p> : null}
      {!tool.outcome ? <p>等待工具返回。</p> : null}
    </>
  );
}

function RouteDetails({ route, turnNumber }: {
  route: A2AInvocationRecord;
  turnNumber: Map<string, number>;
}) {
  const sender = turnNumber.get(route.sender_turn_id);
  const target = route.target_turn_id ? turnNumber.get(route.target_turn_id) : undefined;
  return (
    <>
      <div className="trace-inspector-heading">
        <strong>{formatAgentName(route.sender_agent_id)} → {formatAgentName(route.target_agent_id)}</strong>
        <span>{route.reply_failed ? "回复失败" : route.reply_text ? "已回复" : route.mode === "fire_and_forget" ? "委派" : "等待回复"}</span>
      </div>
      <p>发起：{sender ? `第 ${sender} 轮` : route.sender_turn_id} · 执行：{target ? `第 ${target} 轮` : "等待接力"}</p>
      <p>请求：{route.purpose || "未填写任务目的"}</p>
      {route.reply_failed ? <p className="trace-error">回复失败：{route.reply_text || "没有返回正文"}</p>
        : route.reply_text ? <p>回复：{route.reply_text}</p>
          : route.mode !== "fire_and_forget" ? <p>等待回复。</p> : null}
    </>
  );
}
