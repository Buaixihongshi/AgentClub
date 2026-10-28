import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  it("keeps final discussion results in history while hiding intermediate messages after a follow-up", () => {
    const initial: ThreadEvent = { ...message, id: 'opinion', role: 'agent', content: '阶段内判断', metadata: { discussion_visibility: 'internal' } };
    const final: ThreadEvent = { ...initial, id: 'final', content: '经过复核的最终建议', metadata: { discussion_visibility: 'final', discussion_revision_reviewed: true } };
    const next: ThreadEvent = { ...message, id: 'next', mission_id: 'm2', content: '继续处理', created_at: '2026-10-17T15:38:00Z' };
    const latest: MissionDetail = { mission: mission('m2', 'completed'), messages: [next] };
    const events = buildConversationTimeline({ ...conversation, messages: [message, initial, final], failure_events: [] }, latest);
    expect(events.map((event) => event.id)).toEqual(['u1', 'final', 'next']);
    render(<TurnTimeline turns={buildTurnViews(events)} />);
    expect(screen.getByText('经过复核的最终建议')).toBeInTheDocument();
    expect(screen.getByText('团队讨论结果')).toBeInTheDocument();
    expect(screen.queryByText('阶段内判断')).not.toBeInTheDocument();
  });

  it("shows pending human choices and the revision review limit with the final chat result", () => {
    const final: ThreadEvent = { ...message, id: 'candidate', role: 'agent', content: '已完成复核的候选方案',
      metadata: { discussion_visibility: 'final', discussion_pending_decision: true, discussion_revision_reviewed: false } };
    render(<TurnTimeline turns={buildTurnViews([final])} />);
    expect(screen.getByText('其中的取舍仍待你决定。')).toBeInTheDocument();
    expect(screen.getByText(/修订部分未再次独立复核/)).toBeInTheDocument();
  });

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
    // The live activity replaces the generic waiting text with a single progress line.
    expect(screen.getByText("最近活动：读取文件 · example.ts（已完成）")).toBeInTheDocument();
    expect(screen.queryByText("等待完整回复")).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "原生活动记录" })).not.toBeInTheDocument();
  });

  it("shows an unfinished live activity as the current step", () => {
    const views = buildTurnViews([], [], {
      livePartial: { t1: "" },
      turns: [{ turn_id: "t1", agent_id: "wangye", provider_id: "antigravity", status: "started", attempts: [], message_ids: [] }],
      toolEvents: [{ ...activity, phase: "started" }],
    });
    render(<TurnTimeline turns={views} />);
    expect(screen.getByText("正在执行：读取文件 · example.ts")).toBeInTheDocument();
  });

  it("keeps the live activity line after the agent has streamed some text", () => {
    const views = buildTurnViews([], [], {
      livePartial: { t1: "我先读取文件。" },
      turns: [{ turn_id: "t1", agent_id: "wangye", provider_id: "antigravity", status: "started", attempts: [], message_ids: [] }],
      toolEvents: [{ ...activity, phase: "started" }],
    });
    render(<TurnTimeline turns={views} />);
    expect(screen.getByText("我先读取文件。")).toBeInTheDocument();
    expect(screen.getByText("正在执行：读取文件 · example.ts")).toBeInTheDocument();
  });

  it("moves native activity of a finished reply into the thought block tool list", async () => {
    const reply: ThreadEvent = { id: "r1", mission_id: "m1", turn_id: "t1", agent_id: "wangye", role: "agent", event_type: "message", content: "已读完", created_at: "2026-10-17T15:35:00Z" };
    render(<TurnTimeline turns={buildTurnViews([reply], [], { toolEvents: [activity] })} />);
    expect(screen.queryByText(/最近活动/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "含工具 · 1" }));
    expect(screen.getByText("读取文件 · example.ts")).toBeInTheDocument();
  });
});
