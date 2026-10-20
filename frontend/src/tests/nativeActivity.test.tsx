import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NativeActivity } from "../components/NativeActivity";
import { buildTurnViews } from "../lib/missionView";
import type { ThreadEvent, ToolEventRecord } from "../types";

const failure: ThreadEvent = {
  id: "failure:m1:t1", mission_id: "m1", turn_id: "t1", event_type: "turn.failure",
  role: "system", content: "冯宝宝本轮超时，未发布回复。", created_at: "2026-10-20T09:16:06Z",
};
const start: ToolEventRecord = {
  id: 1, mission_id: "m1", turn_id: "t1", agent_id: "baobao", provider_id: "claude",
  event_type: "provider.activity", tool_name: "Bash", activity_id: "call1", phase: "started",
  created_at: "2026-10-20T09:11:39Z",
};
const argumentsReady: ToolEventRecord = {
  ...start, id: 2, phase: "completed", summary: "Bash · npm test", args: { command: "npm test" },
  created_at: "2026-10-20T09:11:40Z",
};
const result: ToolEventRecord = {
  ...start, id: 3, phase: "result", ok: true, output: "257 passed",
  created_at: "2026-10-20T09:11:44Z",
};

function calls(events: ToolEventRecord[]) {
  return buildTurnViews([failure], [], { toolEvents: events })[0].toolCalls!;
}

describe("native activity lifecycle", () => {
  it("shows a successful Claude result as completed after timeout, retaining its command and arguments", () => {
    const merged = calls([start, argumentsReady, result]);
    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({ phase: "completed", summary: "Bash · npm test", toolInput: { command: "npm test" } });
    const { rerender } = render(<NativeActivity calls={merged} />);
    expect(screen.getByText("最近活动：Bash · npm test（已完成）")).toBeInTheDocument();
    expect(screen.queryByText(/未收到完成确认/)).not.toBeInTheDocument();
    // Full replay after refresh gives the same result without altering history.
    rerender(<NativeActivity calls={calls([start, argumentsReady, result])} />);
    expect(within(screen.getByRole("list", { name: "原生活动记录" })).getAllByRole("listitem")).toHaveLength(1);
  });

  it("does not claim completion when Claude has only generated the tool input", () => {
    const merged = calls([start, argumentsReady]);
    const { rerender } = render(<NativeActivity calls={merged} live />);
    expect(screen.getByText("最近活动：Bash · npm test（进行中）")).toBeInTheDocument();
    rerender(<NativeActivity calls={merged} />);
    expect(screen.getByText("最近活动：Bash · npm test（未收到完成确认）")).toBeInTheDocument();
  });

  it("shows failed results as unfinished while retaining their descriptions", () => {
    render(<NativeActivity calls={calls([start, argumentsReady, { ...result, phase: "failed", ok: false }])} />);
    expect(screen.getByText("最近活动：Bash · npm test（未完成）")).toBeInTheDocument();
  });

  it("does not treat a result without a success flag as confirmed", () => {
    expect(calls([start, argumentsReady, { ...result, ok: undefined }])[0].phase).toBe("started");
    expect(calls([start, argumentsReady, { ...result, ok: false }])[0].phase).toBe("failed");
  });

  it("keeps other providers' completion semantics and separate activity identities", () => {
    const merged = calls([start, argumentsReady, result, {
      ...argumentsReady, id: 4, activity_id: "agy2", provider_id: "antigravity", summary: "读取文件 · demo.html",
    }]);
    expect(merged.map((call) => call.phase)).toEqual(["completed", "completed"]);
    expect(merged.map((call) => call.activityId)).toEqual(["call1", "agy2"]);
  });
});
