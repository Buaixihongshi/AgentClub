import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { buildConversationTimeline } from "../lib/conversationTimeline";
import { buildTurnViews } from "../lib/missionView";
import { TurnTimeline } from "../components/TurnTimeline";
import type { ConversationDetail, MissionDetail, ThreadEvent, ToolEventRecord } from "../types";

const mission = (id: string, status: "blocked" | "completed") => ({ id, goal: "test", status, created_at: "2026-10-17T15:30:00Z", updated_at: "2026-10-17T15:35:00Z" });
const message: ThreadEvent = { id: "u1", mission_id: "m1", role: "user", event_type: "message", content: "先执行", created_at: "2026-10-17T15:30:00Z" };
const failure: ThreadEvent = { id: "failure:m1:t1", mission_id: "m1", turn_id: "t1", agent_id: "wangye", provider_id: "antigravity", role: "system", event_type: "turn.failure", content: "王也本轮超时，未发布回复。", created_at: "2026-10-17T15:35:00+00:00" };
const activity: ToolEventRecord = { id: "a1", mission_id: "m1", turn_id: "t1", agent_id: "wangye", event_type: "provider.activity", tool_name: "view_file", activity_id: "s:2", phase: "completed", summary: "读取文件 · example.ts", created_at: "2026-10-17T15:34:00Z" };
const conversation: ConversationDetail = { conversation: { id: "c1", title: "test", status: "active", created_at: message.created_at, updated_at: failure.created_at }, missions: [mission("m1", "blocked")], messages: [message], failure_events: [failure] };

describe("persistent failure timeline", () => {
  it("keeps the notice between the original message and follow-up after refresh", () => {
    const next: ThreadEvent = { ...message, id: "u2", mission_id: "m2", content: "继续", created_at: "2026-10-17T15:38:00Z" };
    const latest: MissionDetail = { mission: mission("m2", "completed"), messages: [next], failure_events: [] };
    const first = buildConversationTimeline(conversation, latest);
    const refreshed = buildConversationTimeline({ ...conversation, messages: [message, next] }, latest);
    expect(first.map((event) => event.id)).toEqual([message.id, failure.id, next.id]);
    expect(refreshed).toEqual(first);
    const { rerender } = render(<TurnTimeline turns={buildTurnViews(first, [], { toolEvents: [activity] })} />);
    expect(screen.getByText(failure.content)).toBeInTheDocument();
    expect(screen.getByText(/最近活动：读取文件/)).toBeInTheDocument();
    rerender(<TurnTimeline turns={buildTurnViews(refreshed, [], { toolEvents: [activity] })} />);
    expect(screen.getAllByText(failure.content)).toHaveLength(1);
  });

  it("deduplicates current snapshot notices and removes stale failure after recovery", () => {
    const latest: MissionDetail = { mission: mission("m1", "blocked"), messages: [message], failure_events: [failure] };
    expect(buildConversationTimeline(conversation, latest)).toHaveLength(2);
    expect(buildConversationTimeline(conversation, { ...latest, mission: mission("m1", "completed"), failure_events: [] })).toEqual([message]);
  });

  it("merges native start and completion into one activity during a live turn", () => {
    const views = buildTurnViews([], [], {
      livePartial: { t1: "" },
      turns: [{ turn_id: "t1", agent_id: "wangye", provider_id: "antigravity", status: "started", attempts: [], message_ids: [] }],
      toolEvents: [{ ...activity, phase: "started" }, { ...activity, id: "a2", phase: "completed" }],
    });
    expect(views[0].toolCalls).toHaveLength(1);
    render(<TurnTimeline turns={views} />);
    expect(screen.getByText(/最近活动：读取文件 · example.ts（已完成）/)).toBeInTheDocument();
    expect(screen.getByText("等待完整回复")).toBeInTheDocument();
  });
});
