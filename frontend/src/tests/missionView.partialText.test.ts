import { describe, expect, it } from "vitest";
import { buildTurnViews } from "../lib/missionView";
import type { ThreadEvent, TurnRecord } from "../types";

const baseTurnRecord: TurnRecord = {
  turn_id: "turn_active",
  agent_id: "wangye",
  provider_id: "gemini",
  status: "started",
  attempts: [],
  started_at: "2026-05-13T00:00:00Z",
  message_ids: [],
};

describe("buildTurnViews with live_partial", () => {
  it("appends an in-flight TurnView with partialText for a started turn that has no agent message yet", () => {
    const views = buildTurnViews(
      [],
      [],
      { livePartial: { turn_active: "我正在思考" }, turns: [baseTurnRecord] },
    );
    const inflight = views.find((v) => v.id === "live:turn_active");
    expect(inflight).toBeDefined();
    expect(inflight?.partialText).toBe("我正在思考");
    expect(inflight?.role).toBe("agent");
    expect(inflight?.agentId).toBe("wangye");
    expect(inflight?.providerLabel).toBe("gemini");
    expect(inflight?.eventType).toBe("message.partial");
  });

  it("does not append in-flight bubble when an agent message event already exists for that turn_id", () => {
    const event: ThreadEvent = {
      id: "msg_1",
      mission_id: "m1",
      event_type: "message",
      role: "agent",
      content: "已发声",
      agent_id: "wangye",
      provider_id: "gemini",
      turn_id: "turn_active",
      metadata: { turn_id: "turn_active", provider_id: "gemini" },
      created_at: "2026-05-13T00:00:01Z",
    };
    const views = buildTurnViews(
      [event],
      [],
      { livePartial: { turn_active: "stale" }, turns: [baseTurnRecord] },
    );
    expect(views.find((v) => v.id === "live:turn_active")).toBeUndefined();
    expect(views[0].content).toBe("已发声");
    expect(views[0].partialText).toBeUndefined();
  });

  it("does not append in-flight bubble for completed turns", () => {
    const completed: TurnRecord = { ...baseTurnRecord, status: "completed" };
    const views = buildTurnViews(
      [],
      [],
      { livePartial: { turn_active: "stale" }, turns: [completed] },
    );
    expect(views.find((v) => v.id === "live:turn_active")).toBeUndefined();
  });

  it("appends an in-flight bubble even when partial text is empty (Gemini/Codex have no token deltas)", () => {
    const views = buildTurnViews(
      [],
      [],
      { livePartial: { turn_active: "" }, turns: [baseTurnRecord] },
    );
    const inflight = views.find((v) => v.id === "live:turn_active");
    expect(inflight).toBeDefined();
    expect(inflight?.partialText).toBe("");
    expect(inflight?.eventType).toBe("message.partial");
  });

  it("works without options (existing call sites unaffected)", () => {
    const views = buildTurnViews([], []);
    expect(views).toEqual([]);
  });
});
