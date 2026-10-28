import type { StreamState } from "../hooks/useMissionStream";
import type { MissionRuntimeSummary } from "../types";

// Routine states (completed, live stream, turn count) are visible in the room itself; the header only
// surfaces what needs the user's attention.
const ATTENTION_STATUS = new Set(["等待你的选择", "等待你的决定"]);

export function MissionHeader(props: {
  runtimeSummary: MissionRuntimeSummary;
  streamState: StreamState;
  showRuntime?: boolean;
}) {
  const notices = [
    ATTENTION_STATUS.has(props.runtimeSummary.statusLabel) ? props.runtimeSummary.statusLabel : null,
    props.streamState === "reconnecting" ? "实时流：重连中" : null,
  ].filter((notice): notice is string => Boolean(notice));

  return (
    <header className="mission-header">
      <div>
        <h1>{props.runtimeSummary.title}</h1>
      </div>
      {props.showRuntime !== false && notices.length > 0 && <div className="header-status" role="status">
        {notices.map((notice) => <span key={notice}>{notice}</span>)}
      </div>}
    </header>
  );
}
