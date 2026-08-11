import { describe, expect, it } from "vitest";
import {
  buildAgentParticipants,
  buildMissionRuntimeSummary,
  buildProviderIssueSummary,
  buildTurnViews,
  findLatestAgentTurn,
  formatMissionStatus,
  formatTurnIdentity,
} from "../lib/missionView";
import type { A2AInvocationRecord, MissionDetail, ThreadEvent, TurnView } from "../types";

describe("formatTurnIdentity", () => {
  it("shows persona name prominently with role hint and provider", () => {
    expect(
      formatTurnIdentity({
        capability: "review",
        providerLabel: "Claude Code",
        personaLabel: "王也",
      }),
    ).toBe("王也（Claude）");
  });

  it("falls back to capability label when no persona", () => {
    expect(
      formatTurnIdentity({
        capability: "planning",
        providerLabel: "Gemini CLI",
      }),
    ).toBe("Planning turn · Gemini CLI");
  });

  it("omits role hint for unknown capability with persona", () => {
    expect(
      formatTurnIdentity({
        capability: "unknown",
        providerLabel: "Fake CLI",
        personaLabel: "测试角色",
      }),
    ).toBe("测试角色（演示）");
  });
});

describe("buildTurnViews", () => {
  it("marks system events as status with turnIndex 0", () => {
    const events: ThreadEvent[] = [
      {
        id: 1, mission_id: "m1", event_type: "status", role: "system",
        content: "Mission started", created_at: "2026-04-25T00:00:00Z",
      },
      {
        id: 2, mission_id: "m1", event_type: "message", role: "agent",
        content: "分析完毕。", agent_id: "wangye", created_at: "2026-04-25T00:01:00Z",
        metadata: { capability: "review", provider_label: "Gemini CLI", persona_label: "王也" },
      },
      {
        id: 3, mission_id: "m1", event_type: "status", role: "system",
        content: "Mission completed", created_at: "2026-04-25T00:02:00Z",
      },
    ];
    const turns = buildTurnViews(events);
    expect(turns[0].isStatusEvent).toBe(true);
    expect(turns[0].turnIndex).toBe(0);
    expect(turns[1].isStatusEvent).toBe(false);
    expect(turns[1].turnIndex).toBe(1);
    expect(turns[1].agentId).toBe("wangye");
    expect(turns[2].isStatusEvent).toBe(true);
    expect(turns[2].turnIndex).toBe(0);
  });

  it("numbers agent turns independently", () => {
    const events: ThreadEvent[] = [
      { id: 1, mission_id: "m1", event_type: "message", role: "agent", content: "规划", agent_id: "chulan", created_at: "2026-04-25T00:00:00Z", metadata: { persona_label: "张楚岚", provider_label: "Claude Code", capability: "planning" } },
      { id: 2, mission_id: "m1", event_type: "message", role: "agent", content: "执行", agent_id: "baobao", created_at: "2026-04-25T00:01:00Z", metadata: { persona_label: "冯宝宝", provider_label: "Codex CLI", capability: "implementation" } },
      { id: 3, mission_id: "m1", event_type: "message", role: "agent", content: "复审", agent_id: "wangye", created_at: "2026-04-25T00:02:00Z", metadata: { persona_label: "王也", provider_label: "Gemini CLI", capability: "review" } },
    ];
    const turns = buildTurnViews(events);
    expect(turns[0].turnIndex).toBe(1);
    expect(turns[1].turnIndex).toBe(2);
    expect(turns[2].turnIndex).toBe(3);
  });

  it("handles agent.message event_type", () => {
    const event: ThreadEvent = {
      id: 1, mission_id: "m1", event_type: "agent.message", role: "agent",
      content: "先从风险看。", created_at: "2026-04-25T00:00:00Z",
      metadata: { capability: "review", provider_label: "Claude Code", persona_label: "王也" },
    };
    const turns = buildTurnViews([event]);
    expect(turns[0].isStatusEvent).toBe(false);
    expect(turns[0].turnIndex).toBe(1);
  });

  it("treats interrupt events as user messages, not status events", () => {
    const event: ThreadEvent = {
      id: 5, mission_id: "m1", event_type: "interrupt", role: "user",
      content: "@王也 看风险", created_at: "2026-04-25T00:03:00Z",
    };
    const turns = buildTurnViews([event]);
    expect(turns[0].isStatusEvent).toBe(false);
    expect(turns[0].role).toBe("user");
  });

  it("falls back to provider id, character name, and inferred capability", () => {
    const event: ThreadEvent = {
      id: 2, mission_id: "m1", event_type: "agent.message", role: "agent",
      content: "我先执行产物草案。", provider_id: "codex", character_name: "冯宝宝",
      created_at: "2026-04-25T00:00:00Z",
    };
    const turns = buildTurnViews([event]);
    expect(turns[0].capability).toBe("implementation");
    expect(turns[0].providerLabel).toBe("codex");
    expect(turns[0].personaLabel).toBe("冯宝宝");
  });

  it("attaches thought events to the matching turn in created_at order only", () => {
    const events: ThreadEvent[] = [
      {
        id: 1,
        mission_id: "m1",
        event_type: "message",
        role: "agent",
        content: "第一轮正文",
        agent_id: "chulan",
        turn_id: "turn_1",
        created_at: "2026-04-25T00:00:00Z",
      },
      {
        id: 2,
        mission_id: "m1",
        event_type: "message",
        role: "agent",
        content: "第二轮正文",
        agent_id: "baobao",
        turn_id: "turn_2",
        created_at: "2026-04-25T00:01:00Z",
      },
      {
        id: 3,
        mission_id: "m1",
        event_type: "agent.thought",
        role: "system",
        content: "后到但时间晚",
        turn_id: "turn_1",
        metadata: { kind: "plan", tags: ["ui"] },
        created_at: "2026-04-25T00:00:04Z",
      },
      {
        id: 4,
        mission_id: "m1",
        event_type: "agent.thought",
        role: "system",
        content: "先显示",
        turn_id: "turn_1",
        metadata: { kind: "reasoning" },
        created_at: "2026-04-25T00:00:02Z",
      },
      {
        id: 5,
        mission_id: "m1",
        event_type: "agent.thought",
        role: "system",
        content: "别串到第一轮",
        turn_id: "turn_2",
        created_at: "2026-04-25T00:01:02Z",
      },
    ];

    const turns = buildTurnViews(events);

    expect(turns).toHaveLength(2);
    expect(turns[0].thoughts).toEqual([
      {
        content: "先显示",
        kind: "reasoning",
        tags: undefined,
        createdAt: "2026-04-25T00:00:02Z",
      },
      {
        content: "后到但时间晚",
        kind: "plan",
        tags: ["ui"],
        createdAt: "2026-04-25T00:00:04Z",
      },
    ]);
    expect(turns[1].thoughts?.map((thought) => thought.content)).toEqual(["别串到第一轮"]);
  });

  it("attaches mission detail thought records without rendering them as turns", () => {
    const events: ThreadEvent[] = [
      {
        id: 1,
        mission_id: "m1",
        event_type: "message",
        role: "agent",
        content: "正文",
        agent_id: "chulan",
        turn_id: "turn_1",
        created_at: "2026-04-25T00:00:00Z",
      },
    ];

    const turns = buildTurnViews(events, [], {
      thoughts: [
        {
          turn_id: "turn_1",
          content: "详情思考",
          kind: "plan",
          tags: ["fake"],
          created_at: "2026-04-25T00:00:01Z",
        },
        {
          turn_id: "turn_other",
          content: "别进来",
          created_at: "2026-04-25T00:00:02Z",
        },
      ],
    });

    expect(turns).toHaveLength(1);
    expect(turns[0].thoughts).toEqual([
      {
        content: "详情思考",
        kind: "plan",
        tags: ["fake"],
        createdAt: "2026-04-25T00:00:01Z",
      },
    ]);
  });
});

describe("buildMissionRuntimeSummary", () => {
  it("uses a stable room title instead of echoing the mission goal", () => {
    const summary = buildMissionRuntimeSummary({
      mission: {
        id: "m1",
        goal: "什么是 Codex CLI？",
        title: "什么是 Codex CLI？",
        status: "completed",
        created_at: "2026-05-11T00:00:00Z",
        updated_at: "2026-05-11T00:01:00Z",
      },
      events: [],
      artifacts: [],
    }, []);

    expect(summary.title).toBe("AgentClub 任务室");
  });
});

describe("findLatestAgentTurn", () => {
  it("skips status events and returns the last agent message", () => {
    const events: ThreadEvent[] = [
      { id: 1, mission_id: "m1", event_type: "message", role: "agent", content: "拆解", agent_id: "chulan", created_at: "2026-04-25T00:00:00Z", metadata: { persona_label: "张楚岚", provider_label: "Claude Code", capability: "planning" } },
      { id: 2, mission_id: "m1", event_type: "status", role: "system", content: "completed", created_at: "2026-04-25T00:01:00Z" },
    ];
    const turns = buildTurnViews(events);
    const latest = findLatestAgentTurn(turns);
    expect(latest?.agentId).toBe("chulan");
    expect(latest?.personaLabel).toBe("张楚岚");
  });

  it("returns undefined when no agent turns exist", () => {
    const turns = buildTurnViews([]);
    expect(findLatestAgentTurn(turns)).toBeUndefined();
  });
});

describe("buildAgentParticipants", () => {
  it("aggregates agent participation from turns", () => {
    const events: ThreadEvent[] = [
      { id: 1, mission_id: "m1", event_type: "message", role: "agent", content: "规划", agent_id: "chulan", created_at: "2026-04-25T00:00:00Z", metadata: { persona_label: "张楚岚", provider_label: "Claude Code", capability: "planning" } },
      { id: 2, mission_id: "m1", event_type: "message", role: "agent", content: "执行", agent_id: "baobao", created_at: "2026-04-25T00:01:00Z", metadata: { persona_label: "冯宝宝", provider_label: "Codex CLI", capability: "implementation" } },
    ];
    const turns = buildTurnViews(events);
    const participants = buildAgentParticipants(turns);
    expect(participants).toHaveLength(3);
    const chulan = participants.find((p) => p.agentId === "chulan")!;
    expect(chulan.participated).toBe(true);
    expect(chulan.personaLabel).toBe("张楚岚");
    expect(chulan.turnIndex).toBe(1);
    const baobao = participants.find((p) => p.agentId === "baobao")!;
    expect(baobao.participated).toBe(true);
    const wangye = participants.find((p) => p.agentId === "wangye")!;
    expect(wangye.participated).toBe(false);
    expect(wangye.turnIndex).toBe(0);
  });

  it("uses the latest turn for each agent, not the first", () => {
    const events: ThreadEvent[] = [
      { id: 1, mission_id: "m1", event_type: "message", role: "agent", content: "规划v1", agent_id: "chulan", created_at: "2026-04-25T00:00:00Z", metadata: { persona_label: "张楚岚", provider_label: "Claude Code", capability: "planning" } },
      { id: 2, mission_id: "m1", event_type: "message", role: "agent", content: "规划v2", agent_id: "chulan", created_at: "2026-04-25T00:03:00Z", metadata: { persona_label: "张楚岚", provider_label: "Gemini CLI", capability: "coordination" } },
    ];
    const turns = buildTurnViews(events);
    const participants = buildAgentParticipants(turns);
    const chulan = participants.find((p) => p.agentId === "chulan")!;
    expect(chulan.providerLabel).toBe("Gemini CLI");
    expect(chulan.capability).toBe("coordination");
    expect(chulan.turnIndex).toBe(2);
  });
});

it("formats mission status labels in Chinese", () => {
  expect(formatMissionStatus("queued")).toBe("排队中");
  expect(formatMissionStatus("running")).toBe("运行中");
  expect(formatMissionStatus("completed_with_warnings")).toBe("已完成（有警告）");
  expect(formatMissionStatus("unknown-state")).toBe("unknown-state");
});

it("builds a runtime summary from detail and turns", () => {
  const detail: MissionDetail = {
    mission: {
      id: "m1",
      goal: "真实 CLI 对话",
      title: "CLI smoke",
      status: "running",
      complexity: "complex",
      created_at: "2026-04-30T01:00:00Z",
      updated_at: "2026-04-30T01:02:00Z",
    },
    events: [],
    artifacts: [],
  };
  const turns: TurnView[] = [
    {
      id: "u1",
      turnIndex: 0,
      role: "user",
      eventType: "message",
      content: "开始",
      capability: "unknown",
      providerLabel: "Provider pending",
      createdAt: "2026-04-30T01:00:00Z",
      metadata: {},
      isStatusEvent: false,
    },
    {
      id: "a1",
      turnIndex: 1,
      role: "agent",
      eventType: "message",
      content: "收到",
      capability: "planning",
      providerLabel: "Gemini CLI",
      personaLabel: "张楚岚",
      agentId: "chulan",
      createdAt: "2026-04-30T01:01:00Z",
      metadata: {},
      isStatusEvent: false,
    },
  ];

  expect(buildMissionRuntimeSummary(detail, turns)).toEqual({
    title: "AgentClub 任务室",
    statusLabel: "运行中",
    complexityLabel: "complex",
    agentTurnCount: 1,
    artifactCount: 0,
    updatedAt: "2026-04-30T01:02:00Z",
  });
});

it("summarizes provider issues from diagnostics", () => {
  expect(
    buildProviderIssueSummary([
      { id: "claude", label: "Claude Code", enabled: true, available: false, error: "429 quota" },
      { id: "codex", label: "Codex CLI", enabled: true, available: true },
      { id: "gemini", label: "Gemini CLI", enabled: false, available: false },
    ]),
  ).toEqual(["Claude Code：429 quota"]);
});

it("attaches A2A invocation metadata to the invoked target turn", () => {
  const events: ThreadEvent[] = [
    {
      id: 1,
      mission_id: "m1",
      event_type: "message",
      role: "agent",
      content: "我叫宝宝来",
      provider_id: "codex",
      agent_id: "chulan",
      turn_id: "turn_1",
      metadata: { provider_label: "Codex CLI", persona_label: "张楚岚" },
      created_at: "2026-05-11T00:00:00Z",
    },
    {
      id: 2,
      mission_id: "m1",
      event_type: "message",
      role: "agent",
      content: "我来核对",
      provider_id: "codex",
      agent_id: "baobao",
      turn_id: "turn_2",
      metadata: { provider_label: "Codex CLI", persona_label: "冯宝宝" },
      created_at: "2026-05-11T00:00:01Z",
    },
  ];

  const turns = buildTurnViews(events, [
    {
      event_id: 99,
      sender_agent_id: "chulan",
      sender_turn_id: "turn_1",
      target_agent_id: "baobao",
      purpose: "核对异闻物档案",
      mode: "fire_and_forget",
      queued_position: 1,
      created_at: "2026-05-11T00:00:00Z",
    },
  ]);

  expect(turns[1].a2aInvocation?.purpose).toBe("核对异闻物档案");
});

it("attaches A2A arrow precisely by target_turn_id even when target agent spoke before", () => {
  // baobao spoke first in turn_0 (before the A2A call), then was invoked via A2A and spoke in turn_2
  const events: ThreadEvent[] = [
    {
      id: 1,
      mission_id: "m1",
      event_type: "message",
      role: "agent",
      content: "宝宝第一次发言",
      provider_id: "codex",
      agent_id: "baobao",
      turn_id: "turn_0",
      metadata: { provider_label: "Codex CLI", persona_label: "冯宝宝" },
      created_at: "2026-05-11T00:00:00Z",
    },
    {
      id: 2,
      mission_id: "m1",
      event_type: "message",
      role: "agent",
      content: "楚岚发言并召唤",
      provider_id: "claude",
      agent_id: "chulan",
      turn_id: "turn_1",
      metadata: { provider_label: "Claude Code", persona_label: "张楚岚" },
      created_at: "2026-05-11T00:00:01Z",
    },
    {
      id: 3,
      mission_id: "m1",
      event_type: "message",
      role: "agent",
      content: "宝宝A2A接力发言",
      provider_id: "codex",
      agent_id: "baobao",
      turn_id: "turn_2",
      metadata: { provider_label: "Codex CLI", persona_label: "冯宝宝" },
      created_at: "2026-05-11T00:00:02Z",
    },
  ];

  const invocation: A2AInvocationRecord = {
    event_id: 99,
    sender_agent_id: "chulan",
    sender_turn_id: "turn_1",
    target_agent_id: "baobao",
    target_turn_id: "turn_2",  // precise match to the A2A turn
    purpose: "核对异闻物档案",
    mode: "fire_and_forget",
    queued_position: 1,
    created_at: "2026-05-11T00:00:01Z",
  };

  const turns = buildTurnViews(events, [invocation]);

  // turn_0 (first baobao turn, before A2A) should NOT have the arrow
  expect(turns[0].a2aInvocation).toBeUndefined();
  // turn_1 (chulan) should not have an arrow
  expect(turns[1].a2aInvocation).toBeUndefined();
  // turn_2 (A2A-invoked baobao turn) should have the arrow
  expect(turns[2].a2aInvocation?.purpose).toBe("核对异闻物档案");
  expect(turns[2].a2aInvocation?.target_turn_id).toBe("turn_2");
});

it("keeps unmatched A2A delivery tasks visible as standalone timeline entries", () => {
  const events: ThreadEvent[] = [
    {
      id: 1,
      mission_id: "m1",
      event_type: "message",
      role: "agent",
      content: "楚岚发起接力",
      provider_id: "claude",
      agent_id: "chulan",
      turn_id: "turn_1",
      metadata: { provider_label: "Claude Code", persona_label: "张楚岚" },
      created_at: "2026-05-11T00:00:00Z",
    },
  ];

  const turns = buildTurnViews(events, [
    {
      event_id: 99,
      sender_agent_id: "chulan",
      sender_turn_id: "turn_1",
      target_agent_id: "wangye",
      target_turn_id: "turn_missing",
      purpose: "摸清现状数据层",
      mode: "fire_and_forget",
      queued_position: 1,
      created_at: "2026-05-11T00:00:01Z",
    },
  ]);

  expect(turns).toHaveLength(2);
  expect(turns[1].id).toBe("a2a:99");
  expect(turns[1].a2aInvocation?.purpose).toBe("摸清现状数据层");
  expect(turns[1].content).toBe("等待王也接力");
});
