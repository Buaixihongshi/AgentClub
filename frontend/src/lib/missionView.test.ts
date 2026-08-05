import { describe, expect, it } from "vitest";
import type { ThreadEvent, TurnAttempt, TurnRecord } from "../types";
import { buildTurnViews, deriveTurnPath } from "./missionView";

function makeTurn(attempts: Partial<TurnAttempt>[]): TurnRecord {
  return {
    turn_id: "t_1",
    agent_id: "baobao",
    provider_id: "codex",
    status: "completed",
    attempts: attempts.map((a, i) => ({
      attempt: i + 1,
      provider_id: "codex",
      event_type: "turn.completed",
      ...a,
    })),
    started_at: null,
    completed_at: null,
    duration_ms: null,
    message_ids: [],
  };
}

describe("deriveTurnPath", () => {
  it('returns "mcp" when any attempt summary contains "via MCP"', () => {
    const turn = makeTurn([{ summary: "via MCP" }]);
    expect(deriveTurnPath(turn)).toBe("mcp");
  });

  it('returns "mcp" for partial match inside a longer summary string', () => {
    const turn = makeTurn([{ summary: "codex completed turn via MCP (1 tool)" }]);
    expect(deriveTurnPath(turn)).toBe("mcp");
  });

  it('returns "mcp" even if a later attempt also has stdout', () => {
    const turn = makeTurn([
      { summary: "via MCP", provider_id: "codex" },
      { summary: "stdout", provider_id: "codex" },
    ]);
    expect(deriveTurnPath(turn)).toBe("mcp");
  });

  it('returns "stdout" when summary contains "stdout" but not "via MCP"', () => {
    const turn = makeTurn([{ summary: "stdout" }]);
    expect(deriveTurnPath(turn)).toBe("stdout");
  });

  it('returns "stdout" for "stdout envelope" summary', () => {
    const turn = makeTurn([{ summary: "stdout envelope" }]);
    expect(deriveTurnPath(turn)).toBe("stdout");
  });

  it('returns "fake" when there are no attempts', () => {
    const turn = makeTurn([]);
    expect(deriveTurnPath(turn)).toBe("fake");
  });

  it('returns "fake" when attempt summary is undefined', () => {
    const turn = makeTurn([{ summary: undefined }]);
    expect(deriveTurnPath(turn)).toBe("fake");
  });

  it('returns "fake" when attempt summary is some other string', () => {
    const turn = makeTurn([{ summary: "fake adapter response" }]);
    expect(deriveTurnPath(turn)).toBe("fake");
  });

  it('returns "fake" when summary contains neither "via MCP" nor "stdout"', () => {
    const turn = makeTurn([{ summary: "" }]);
    expect(deriveTurnPath(turn)).toBe("fake");
  });
});

describe("buildTurnViews thought aggregation", () => {
  const events: ThreadEvent[] = [
    {
      id: 1,
      mission_id: "m1",
      event_type: "message",
      role: "agent",
      content: "Hello",
      turn_id: "t1",
      agent_id: "baobao",
      created_at: "2026-05-23T10:00:00Z",
    },
    {
      id: 2,
      mission_id: "m1",
      event_type: "thought",
      role: "agent",
      content: "I am thinking",
      turn_id: "t1",
      created_at: "2026-05-23T09:59:59Z",
    },
    {
      id: 3,
      mission_id: "m1",
      event_type: "thought",
      role: "agent",
      content: "I am planning",
      turn_id: "t1",
      metadata: { kind: "plan" },
      created_at: "2026-05-23T09:59:58Z",
    },
    {
      id: 4,
      mission_id: "m1",
      event_type: "message",
      role: "agent",
      content: "Next step",
      turn_id: "t2",
      agent_id: "baobao",
      created_at: "2026-05-23T10:05:00Z",
    },
    {
      id: 5,
      mission_id: "m1",
      event_type: "thought",
      role: "agent",
      content: "Thinking about next step",
      turn_id: "t2",
      created_at: "2026-05-23T10:04:59Z",
    },
  ];

  it("aggregates thoughts by turn_id and sorts them by createdAt", () => {
    const views = buildTurnViews(events);
    const t1View = views.find((v) => v.id === "1");
    const t2View = views.find((v) => v.id === "4");

    expect(t1View?.thoughts).toHaveLength(2);
    // Sorted by createdAt: 9:59:58 before 9:59:59
    expect(t1View?.thoughts?.[0].content).toBe("I am planning");
    expect(t1View?.thoughts?.[0].kind).toBe("plan");
    expect(t1View?.thoughts?.[1].content).toBe("I am thinking");

    expect(t2View?.thoughts).toHaveLength(1);
    expect(t2View?.thoughts?.[0].content).toBe("Thinking about next step");
  });

  it("handles options.thoughts supplemental records", () => {
    const views = buildTurnViews(events, [], {
      thoughts: [
        {
          turn_id: "t1",
          content: "Late thought",
          created_at: "2026-05-23T10:00:01Z",
        },
      ],
    });
    const t1View = views.find((v) => v.id === "1");
    expect(t1View?.thoughts).toHaveLength(3);
    expect(t1View?.thoughts?.[2].content).toBe("Late thought");
  });

  it("does not include thought events as main turn views", () => {
    const views = buildTurnViews(events);
    const thoughtViews = views.filter((v) => v.eventType === "thought");
    expect(thoughtViews).toHaveLength(0);
  });
});
