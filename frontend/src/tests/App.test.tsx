import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { ConversationDetail, MissionDetail, MissionStatus } from "../types";

const BACKEND_MISSION_STATUSES: MissionStatus[] = [
  "queued",
  "pending",
  "running",
  "blocked",
  "completed",
  "failed",
  "completed_with_warnings",
];

function makeConvDetail(overrides?: Partial<ConversationDetail>): ConversationDetail {
  return {
    conversation: { id: "c_1", title: "测试", status: "active", created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:00:00Z" },
    missions: [{ id: "m1", goal: "设计一个多 Agent 任务室", status: "queued" as MissionStatus, created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:00:00Z" }],
    messages: [],
    latest_mission_detail: {
      mission: { id: "m1", goal: "设计一个多 Agent 任务室", status: "queued" as MissionStatus, created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:00:00Z" },
      events: [],
      artifacts: [],
    },
    ...overrides,
  };
}

function stubFetch(extraHandlers?: (url: string, init?: RequestInit) => Response | null) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (extraHandlers) {
      const result = extraHandlers(url, init);
      if (result) return result;
    }
    if (url === "/api/providers") {
      return new Response(JSON.stringify([]), { status: 200 });
    }
    if (url === "/api/health") {
      return new Response(JSON.stringify({
        status: "ok",
        version: "0.1.0",
        database: "ready",
        config: "ready",
        fake_provider: "ready",
      }), { status: 200 });
    }
    if (url === "/api/onboarding" && (!init || init.method === "GET")) {
      return new Response(JSON.stringify({
        state: "completed",
        guide_agent_id: "chulan",
        conversation_id: null,
        mission_id: null,
        first_reply_completed_at: null,
        completed_at: "2026-05-05T00:00:00Z",
        verified_provider_ids: [],
        providers: [],
        dismissed_real_cli_setup: true,
      }), { status: 200 });
    }
    if (url.startsWith("/api/conversations?") && (!init || init.method !== "POST")) {
      return new Response(JSON.stringify([]), { status: 200 });
    }
    if (url === "/api/conversations" && init?.method === "POST") {
      return new Response(JSON.stringify({ id: "c_1", title: "新对话", status: "active", created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:00:00Z" }), { status: 200 });
    }
    if (url.match(/\/api\/conversations\/[^/]+\/missions/) && init?.method === "POST") {
      return new Response(JSON.stringify(makeConvDetail()), { status: 200 });
    }
    if (url.match(/\/api\/conversations\/[^/]+$/) && (!init || init.method === "GET")) {
      return new Response(JSON.stringify(makeConvDetail()), { status: 200 });
    }
    if (url === "/api/missions" && init?.method === "POST") {
      return new Response(JSON.stringify({
        mission: { id: "m1", goal: "设计一个多 Agent 任务室", status: "queued", created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:00:00Z" },
        events: [],
        artifacts: [],
      }), { status: 200 });
    }
    if (url === "/api/missions") {
      return new Response(JSON.stringify([]), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
}

/** 等待 selectAt 里 requestAnimationFrame 设置的光标落位，避免后续打字被移动光标打乱顺序。 */
async function flushRaf() {
  await new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

describe("AgentClub app", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.stubGlobal("fetch", stubFetch());
  });

  it("clears a previously matching identity when the backend becomes unreachable", async () => {
    let offline = false;
    const source = __FRONTEND_IDENTITY__;
    vi.stubGlobal("fetch", stubFetch((url) => {
      if (url !== "/api/health") return null;
      if (offline) throw new Error("offline");
      return new Response(JSON.stringify({
        status: "ok", version: "0.1.0", database: "ready", config: "ready", fake_provider: "ready",
        runtime: { started: { ...source, pid: 123, python: "3.12.12", use_fake_cli: true },
          current: source, source_changed: false },
      }), { status: 200 });
    }));
    render(<App />);
    expect(await screen.findByText("启动版本一致")).toBeInTheDocument();
    offline = true;
    window.dispatchEvent(new Event("focus"));
    expect(await screen.findByText("版本信息不完整")).toBeInTheDocument();
    expect(screen.queryByText("启动版本一致")).not.toBeInTheDocument();
  });
  it("creates a mission from the composer via conversation API", async () => {
    render(<App />);
    await userEvent.type(screen.getByLabelText("任务输入"), "设计一个多 Agent 任务室");
    await userEvent.click(screen.getByRole("button", { name: "创建任务" }));
    expect(screen.queryByRole("region", { name: "Agent 参与状态" })).not.toBeInTheDocument();
    expect(await screen.findByText("创建任务后，Agent 轮次会显示在这里。")).toBeInTheDocument();
  });

  it("keeps historical thoughts and tools after a follow-up without duplicating the live mission", async () => {
    const stamp = "2026-05-05T00:00:00Z";
    const details: MissionDetail[] = [1, 2].map((n) => ({
      mission: { id: `m${n}`, goal: "讨论", status: "completed", created_at: stamp, updated_at: stamp },
      messages: [{ id: `msg${n}`, mission_id: `m${n}`, turn_id: `turn${n}`, agent_id: "chulan", role: "agent", event_type: "message", content: `回复${n}`, created_at: stamp }],
      thoughts: [{ id: `thought${n}`, mission_id: `m${n}`, turn_id: `turn${n}`, content: `摘要${n}`, created_at: stamp }],
      tool_events: [{ id: `tool${n}`, mission_id: `m${n}`, turn_id: `turn${n}`, agent_id: "chulan", event_type: "tool.call", tool_name: "publish_message", created_at: stamp }],
    }));
    let count = 0;
    const conversation = () => makeConvDetail({
      messages: details.slice(0, count).flatMap((detail) => detail.messages ?? []),
      thoughts: details.slice(0, count).flatMap((detail) => detail.thoughts ?? []),
      tool_events: details.slice(0, count).flatMap((detail) => detail.tool_events ?? []),
      latest_mission_detail: details[count - 1],
    });
    vi.stubGlobal("fetch", stubFetch((url, init) => {
      if (url === "/api/conversations/c_1/missions" && init?.method === "POST") {
        count += 1;
        return new Response(JSON.stringify(conversation()));
      }
      if (url === "/api/conversations/c_1" && (!init || init.method === "GET")) {
        return new Response(JSON.stringify(conversation()));
      }
      return null;
    }));
    render(<App />);
    for (const n of [1, 2]) {
      await userEvent.type(screen.getByLabelText("任务输入"), `问题${n}`);
      await userEvent.click(screen.getByRole("button", { name: "创建任务" }));
      expect(await screen.findByText(`回复${n}`)).toBeInTheDocument();
    }
    const toggles = screen.getAllByRole("button", { name: "展开思考过程" });
    expect(toggles).toHaveLength(2);
    for (const toggle of toggles) expect(toggle).toHaveTextContent("· 1 条");
    expect(screen.getByText("摘要1")).toBeInTheDocument();
    expect(screen.getByText("摘要2")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "含工具 · 1" })).toHaveLength(2);
  });

  it("starts the first Fake conversation from the onboarding card", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const onboardingReady = {
      state: "ready_for_first_chat",
      guide_agent_id: "chulan",
      conversation_id: null,
      mission_id: null,
      first_reply_completed_at: null,
      completed_at: null,
      verified_provider_ids: [],
      providers: [],
      dismissed_real_cli_setup: false,
    };
    vi.stubGlobal("fetch", stubFetch((url, init) => {
      calls.push({ url, init });
      if (url === "/api/onboarding" && (!init || init.method === "GET")) {
        return new Response(JSON.stringify(onboardingReady), { status: 200 });
      }
      if (url === "/api/onboarding/first-chat" && init?.method === "POST") {
        return new Response(JSON.stringify({
          onboarding: {
            ...onboardingReady,
            state: "first_chat_running",
            conversation_id: "c_welcome",
            mission_id: "m_welcome",
          },
          conversation: makeConvDetail({
            conversation: {
              id: "c_welcome",
              title: "欢迎使用 AgentClub",
              status: "active",
              created_at: "2026-05-05T00:00:00Z",
              updated_at: "2026-05-05T00:00:00Z",
            },
            latest_mission_detail: {
              mission: {
                id: "m_welcome",
                goal: "欢迎",
                status: "running",
                created_at: "2026-05-05T00:00:00Z",
                updated_at: "2026-05-05T00:00:00Z",
              },
              messages: [],
              artifacts: [],
            },
          }),
        }), { status: 202 });
      }
      return null;
    }));

    render(<App />);

    expect(await screen.findByText("AgentClub 已启动")).toBeInTheDocument();
    expect(screen.getByText("演示 Provider 可用")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "和张楚岚打个招呼" }));

    expect(await screen.findByText("张楚岚正在确认聊天室状态…")).toBeInTheDocument();
    expect(calls.some((call) => call.url === "/api/onboarding/first-chat" && call.init?.method === "POST")).toBe(true);
  });

  it("renders backend MissionDetail payloads returned from mission creation", async () => {
    const detail: MissionDetail = {
      mission: {
        id: "m1",
        goal: "实现 Alpha 集成",
        status: "completed",
        created_at: "2026-04-25T00:00:00Z",
        updated_at: "2026-04-25T00:02:00Z",
        completed_at: "2026-04-25T00:02:00Z",
      },
      events: [
        {
          id: 1,
          mission_id: "m1",
          event_type: "agent.message",
          role: "agent",
          content: "已完成实现并通过验证。",
          created_at: "2026-04-25T00:02:00Z",
          metadata: {
            capability: "implementation",
            provider_label: "Codex CLI",
            persona_label: "冯宝宝",
            turn_id: "turn_1",
          },
        },
      ],
      artifacts: [],
    };
    vi.stubGlobal("fetch", stubFetch((url, init) => {
      if (url.match(/\/api\/conversations\/[^/]+\/missions/) && init?.method === "POST") {
        return new Response(JSON.stringify(makeConvDetail({
          latest_mission_detail: detail,
          messages: detail.events ?? [],
        })), { status: 200 });
      }
      if (url.match(/\/api\/conversations\/[^/]+$/) && (!init || init.method === "GET")) {
        return new Response(JSON.stringify(makeConvDetail({
          latest_mission_detail: detail,
          messages: detail.events ?? [],
        })), { status: 200 });
      }
      return null;
    }));

    render(<App />);
    await userEvent.type(screen.getByLabelText("任务输入"), "实现 Alpha 集成");
    await userEvent.click(screen.getByRole("button", { name: "创建任务" }));

    expect(await screen.findByText("已完成实现并通过验证。")).toBeInTheDocument();
    const timeline = screen.getByRole("region", { name: "任务轮次" });
    expect(within(timeline).getByText("冯宝宝（Codex）")).toBeInTheDocument();
    expect(within(timeline).queryByText("via Codex CLI")).not.toBeInTheDocument();
    expect(BACKEND_MISSION_STATUSES).toContain("pending");
  });

  it("confirmed @mention in a running mission sends target_agent_id", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", stubFetch((url, init) => {
      calls.push({ url, init });
      if (url.match(/\/api\/conversations\/[^/]+\/missions/) && init?.method === "POST") {
        return new Response(JSON.stringify(makeConvDetail({
          latest_mission_detail: {
            mission: { id: "m1", goal: "先做规划", status: "running" as MissionStatus, created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:00:00Z" },
            events: [],
            artifacts: [],
          },
        })), { status: 200 });
      }
      if (url === "/api/missions/m1/interruptions") {
        return new Response(JSON.stringify({
          mission: { id: "m1", goal: "先做规划", status: "running", created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:01:00Z" },
          events: [],
          artifacts: [],
        }), { status: 200 });
      }
      return null;
    }));

    render(<App />);
    await userEvent.type(screen.getByLabelText("任务输入"), "先做规划");
    await userEvent.click(screen.getByRole("button", { name: "创建任务" }));
    await screen.findByRole("button", { name: "发送插话" });
    await waitFor(() =>
      expect(screen.getByLabelText("任务输入")).toHaveValue(""),
    );
    await userEvent.type(screen.getByLabelText("任务输入"), "@王");
    await userEvent.keyboard("{Tab}");
    await flushRaf();
    await userEvent.type(screen.getByLabelText("任务输入"), "看风险");
    await userEvent.click(screen.getByRole("button", { name: "发送插话" }));

    const interruptCall = calls.find(
      (call) => call.url === "/api/missions/m1/interruptions",
    );
    expect(JSON.parse(String(interruptCall?.init?.body))).toEqual({
      content: "@王也 看风险",
      target_agent_id: "wangye",
    });
  });

  it("second idle submit calls conversation mission API, not legacy", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", stubFetch((url, init) => {
      calls.push({ url, init });
      if (url.match(/\/api\/conversations\/[^/]+\/missions/) && init?.method === "POST") {
        return new Response(JSON.stringify(makeConvDetail({
          latest_mission_detail: {
            mission: { id: "m1", goal: "test", status: "completed" as MissionStatus, created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:01:00Z", completed_at: "2026-05-05T00:01:00Z" },
            events: [],
            artifacts: [],
          },
        })), { status: 200 });
      }
      return null;
    }));

    render(<App />);
    await userEvent.type(screen.getByLabelText("任务输入"), "第一轮");
    await userEvent.click(screen.getByRole("button", { name: "创建任务" }));
    await waitFor(() => expect(screen.getByLabelText("任务输入")).toHaveValue(""));
    await waitFor(() => expect(screen.getByRole("button", { name: "创建任务" })).toBeInTheDocument());

    await userEvent.type(screen.getByLabelText("任务输入"), "第二轮");
    await userEvent.click(screen.getByRole("button", { name: "创建任务" }));

    const convMissionCalls = calls.filter(c =>
      c.url.match(/\/api\/conversations\/[^/]+\/missions/) && c.init?.method === "POST"
    );
    expect(convMissionCalls.length).toBe(2);
    const legacyMissionCalls = calls.filter(c =>
      c.url === "/api/missions" && c.init?.method === "POST"
    );
    expect(legacyMissionCalls.length).toBe(0);
  });

  it("idle submit with confirmed mention preserves text and sends target_agent_ids", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", stubFetch((url, init) => {
      calls.push({ url, init });
      if (url.match(/\/api\/conversations\/[^/]+\/missions/) && init?.method === "POST") {
        return new Response(JSON.stringify(makeConvDetail({
          latest_mission_detail: {
            mission: { id: "m1", goal: "@王也 看风险", status: "completed" as MissionStatus, created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:01:00Z", completed_at: "2026-05-05T00:01:00Z" },
            events: [],
            artifacts: [],
          },
        })), { status: 200 });
      }
      return null;
    }));

    render(<App />);
    await userEvent.type(screen.getByLabelText("任务输入"), "@王");
    await userEvent.keyboard("{Tab}");
    await flushRaf();
    await userEvent.type(screen.getByLabelText("任务输入"), "看风险");
    await userEvent.click(screen.getByRole("button", { name: "创建任务" }));

    const missionCall = calls.find(c =>
      c.url.match(/\/api\/conversations\/[^/]+\/missions/) && c.init?.method === "POST"
    );
    const body = JSON.parse(String(missionCall?.init?.body));
    expect(body).toEqual({ goal: "@王也 看风险", target_agent_ids: ["wangye"] });
  });

  it("confirmed mention with no body is intercepted before sending", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", stubFetch((url, init) => {
      calls.push({ url, init });
      if (url.match(/\/api\/conversations\/[^/]+\/missions/) && init?.method === "POST") {
        return new Response(JSON.stringify(makeConvDetail()), { status: 200 });
      }
      return null;
    }));

    render(<App />);
    await userEvent.type(screen.getByLabelText("任务输入"), "@王");
    await userEvent.keyboard("{Tab}");

    await userEvent.click(screen.getByRole("button", { name: "创建任务" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("仅 @成员 不能作为任务内容");
    expect(
      calls.some(c => c.url.match(/\/api\/conversations\/[^/]+\/missions/) && c.init?.method === "POST"),
    ).toBe(false);
  });

  it("leading space before a confirmed mention still aligns coordinates", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", stubFetch((url, init) => {
      calls.push({ url, init });
      if (url.match(/\/api\/conversations\/[^/]+\/missions/) && init?.method === "POST") {
        return new Response(JSON.stringify(makeConvDetail()), { status: 200 });
      }
      return null;
    }));

    render(<App />);
    await userEvent.type(screen.getByLabelText("任务输入"), " @王");
    await userEvent.keyboard("{Tab}");

    await userEvent.click(screen.getByRole("button", { name: "创建任务" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("仅 @成员 不能作为任务内容");
    expect(
      calls.some(c => c.url.match(/\/api\/conversations\/[^/]+\/missions/) && c.init?.method === "POST"),
    ).toBe(false);
  });

  it("idle submit does not route plain @mentions without confirmation", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", stubFetch((url, init) => {
      calls.push({ url, init });
      if (url.match(/\/api\/conversations\/[^/]+\/missions/) && init?.method === "POST") {
        return new Response(JSON.stringify(makeConvDetail({
          latest_mission_detail: {
            mission: { id: "m1", goal: "依据经验复盘@张楚岚 @冯宝宝 @王也", status: "completed" as MissionStatus, created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:01:00Z", completed_at: "2026-05-05T00:01:00Z" },
            events: [],
            artifacts: [],
          },
        })), { status: 200 });
      }
      return null;
    }));

    render(<App />);
    await userEvent.type(screen.getByLabelText("任务输入"), "依据经验复盘@张楚岚 @冯宝宝 @王也");
    await userEvent.click(screen.getByRole("button", { name: "创建任务" }));

    const missionCall = calls.find(c =>
      c.url.match(/\/api\/conversations\/[^/]+\/missions/) && c.init?.method === "POST"
    );
    const body = JSON.parse(String(missionCall?.init?.body));
    expect(body).toEqual({
      goal: "依据经验复盘@张楚岚 @冯宝宝 @王也",
    });
  });

  it("running submit with plain @mention does not route target_agent_id", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", stubFetch((url, init) => {
      calls.push({ url, init });
      if (url.match(/\/api\/conversations\/[^/]+\/missions/) && init?.method === "POST") {
        return new Response(JSON.stringify(makeConvDetail({
          latest_mission_detail: {
            mission: { id: "m1", goal: "先做规划", status: "running" as MissionStatus, created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:00:00Z" },
            events: [],
            artifacts: [],
          },
        })), { status: 200 });
      }
      if (url === "/api/missions/m1/interruptions") {
        return new Response(JSON.stringify({
          mission: { id: "m1", goal: "先做规划", status: "running", created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:01:00Z" },
          events: [],
          artifacts: [],
        }), { status: 200 });
      }
      return null;
    }));

    render(<App />);
    await userEvent.type(screen.getByLabelText("任务输入"), "先做规划");
    await userEvent.click(screen.getByRole("button", { name: "创建任务" }));
    await screen.findByRole("button", { name: "发送插话" });
    await waitFor(() => expect(screen.getByLabelText("任务输入")).toHaveValue(""));

    await userEvent.type(screen.getByLabelText("任务输入"), "@冯宝宝 补一刀");
    await userEvent.click(screen.getByRole("button", { name: "发送插话" }));

    const interruptCall = calls.find(c => c.url === "/api/missions/m1/interruptions");
    expect(JSON.parse(String(interruptCall?.init?.body))).toEqual({
      content: "@冯宝宝 补一刀",
    });
  });

  it("clicking new conversation creates and selects an empty conversation", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "+ 新对话" }));
    await waitFor(() => {
      expect(screen.getByRole("navigation", { name: "会话列表" })).toBeInTheDocument();
    });
  });

  it.each(["completed", "blocked", "completed_with_warnings"])(
    "applies SSE snapshots monotonically and closes streams for %s missions",
    async (terminalStatus) => {
    vi.stubGlobal("fetch", stubFetch((url, init) => {
      if (url.match(/\/api\/conversations\/[^/]+\/missions/) && init?.method === "POST") {
        return new Response(JSON.stringify(makeConvDetail({
          latest_mission_detail: {
            mission: { id: "m1", goal: "先做规划", status: "running" as MissionStatus, created_at: "2026-05-05T00:00:00Z", updated_at: "2026-05-05T00:00:00Z" },
            events: [],
            artifacts: [],
          },
        })), { status: 200 });
      }
      return null;
    }));

    render(<App />);
    await userEvent.type(screen.getByLabelText("任务输入"), "先做规划");
    await userEvent.click(screen.getByRole("button", { name: "创建任务" }));
    await screen.findByRole("button", { name: "发送插话" });

    const EventSourceMock = window.EventSource as unknown as {
      instances: Array<EventTarget & { close: ReturnType<typeof vi.fn> }>;
    };
    const source = EventSourceMock.instances[0];
    expect(source).toBeDefined();

    source.dispatchEvent(new MessageEvent("snapshot", { data: "not-json" }));
    expect(await screen.findByText("实时流：重连中")).toBeInTheDocument();

    source.dispatchEvent(
      new MessageEvent("snapshot", {
        data: JSON.stringify({
          mission: {
            id: "m1",
            goal: "先做规划",
            status: "running",
            created_at: "2026-05-05T00:00:00Z",
            updated_at: "2026-05-04T23:59:00Z",
          },
          events: [
            {
              id: 1,
              mission_id: "m1",
              event_type: "agent.message",
              role: "agent",
              content: "旧输出",
              created_at: "2026-05-04T23:59:00Z",
            },
          ],
          artifacts: [],
        }),
      }),
    );
    await waitFor(() => expect(screen.queryByText("旧输出")).not.toBeInTheDocument());

    source.dispatchEvent(
      new MessageEvent("snapshot", {
        data: JSON.stringify({
          mission: {
            id: "m1",
            goal: "先做规划",
            status: terminalStatus,
            created_at: "2026-05-05T00:00:00Z",
            updated_at: "2026-05-05T00:02:00Z",
          },
          events: [
            {
              id: 2,
              mission_id: "m1",
              event_type: "agent.message",
              role: "agent",
              content: "新输出",
              created_at: "2026-05-05T00:02:00Z",
              metadata: {
                capability: "review",
                provider_label: "Claude Code",
                persona_label: "王也",
              },
            },
          ],
          artifacts: [],
        }),
      }),
    );

    expect(await screen.findByText("新输出")).toBeInTheDocument();
    const sseTimeline = screen.getByRole("region", { name: "任务轮次" });
    expect(within(sseTimeline).getByText("第 1 轮")).toBeInTheDocument();
    expect(within(sseTimeline).getByText("王也（Claude）")).toBeInTheDocument();
    expect(within(sseTimeline).queryByText("via Claude Code")).not.toBeInTheDocument();
    await waitFor(() => expect(source.close).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "创建任务" })).toBeInTheDocument();
    },
  );
});
