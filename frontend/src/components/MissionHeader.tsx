import type { StreamState } from "../hooks/useMissionStream";
import type { MissionRuntimeSummary } from "../types";

const STREAM_STATE_LABEL: Record<StreamState, string> = {
  idle: "未连接",
  connecting: "连接中",
  open: "实时同步",
  reconnecting: "重连中",
  closed: "已关闭",
};

export function MissionHeader(props: {
  runtimeSummary: MissionRuntimeSummary;
  streamState: StreamState;
  showRuntime?: boolean;
}) {
  const streamLabel = STREAM_STATE_LABEL[props.streamState] ?? props.streamState;

  return (
    <header className="mission-header">
      <div>
        <h1>{props.runtimeSummary.title}</h1>
      </div>
      {props.showRuntime !== false && <div className="header-status">
        <span>{props.runtimeSummary.statusLabel}</span>
        <span>实时流：{streamLabel}</span>
        <span>{props.runtimeSummary.agentTurnCount} 轮对话</span>
      </div>}
    </header>
  );
}
