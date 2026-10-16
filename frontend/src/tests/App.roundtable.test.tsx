import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type {
  ConversationDetail,
  ConversationSummary,
  DiscussionEndReason,
  DiscussionPhase,
  MissionDetail,
  MissionStatus,
} from "../types";

const GOAL = "Club 下一步该做什么？";
const ALL_PARTICIPANTS = ["chulan", "baobao", "wangye"];

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status });
}

function isMissionCreate(url: string, init?: RequestInit): boolean {
  return /\/api\/conversations\/[^/]+\/missions/.test(url) && init?.method === "POST";
}

function roundtableMission(overrides?: {
  id?: string;
  status?: MissionStatus;
  phase?: DiscussionPhase;
  turnsUsed?: number;
  turnsLimit?: number;
  participants?: string[];
  endReason?: DiscussionEndReason | null;
  pendingControl?: "finish" | "stop" | null;
  summaryMessageIds?: string[];
  unanswered?: Array<number | string>;
  goal?: string;
}): MissionDetail {
  const participants = overrides?.participants ?? ALL_PARTICIPANTS;
  const phase = overrides?.phase ?? "opening";
  return {
    mission: {
      id: overrides?.id ?? "m1",
      goal: overrides?.goal ?? GOAL,
      room_mode: "roundtable",
      discussion: { participant_ids: participants, max_turns: overrides?.turnsLimit ?? 7 },
      status: overrides?.status ?? "running",
      created_at: "2026-10-15T00:00:00Z",
      updated_at: "2026-10-15T00:01:00Z",
    },
    discussion: {
      participant_ids: participants,
      phase,
      turns_used: overrides?.turnsUsed ?? 1,
      turns_limit: overrides?.turnsLimit ?? 7,
      current_speaker: phase === "ended" ? null : participants[0],
      current_turn_id: phase === "ended" ? null : "turn_1",
      pending_control: overrides?.pendingControl ?? null,
      end_reason: overrides?.endReason ?? null,
      summary_message_ids: overrides?.summaryMessageIds ?? [],
      unanswered_interrupt_ids: overrides?.unanswered ?? [],
    },
    messages: [],
    events: [],
    artifacts: [],
  };
}

function makeConversationDetail(overrides?: Partial<ConversationDetail>): ConversationDetail {
  const detail = roundtableMission();
  return {
    conversation: {
      id: "c_1",
      title: "复盘会",
      status: "active",
      created_at: "2026-10-15T00:00:00Z",
      updated_at: "2026-10-15T00:00:00Z",
    },
    missions: [detail.mission],
    messages: [],
    latest_mission_detail: detail,
    ...overrides,
  };
}

function conversationSummary(): ConversationSummary {
  return {
    id: "c_1",
    title: "复盘会",
    status: "active",
    created_at: "2026-10-15T00:00:00Z",
    updated_at: "2026-10-15T00:00:00Z",
    mission_count: 1,
    latest_mission_id: "m1",
  };
}

interface StubConfig {
  conversations?: ConversationSummary[];
  conversationDetail?: ConversationDetail | (() => ConversationDetail);
  missionCreate?: (body: Record<string, unknown>) => Response;
  interrupt?: (body: Record<string, unknown>) => Response;
  control?: (body: Record<string, unknown>) => Response;
}

function stubRoundtableFetch(config: StubConfig = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url === "/api/providers") return json([]);
      if (url === "/api/health") {
        return json({
          status: "ok",
          version: "0.1.0",
          database: "ready",
          config: "ready",
          fake_provider: "ready",
        });
      }
      if (url === "/api/onboarding" && (!init || init.method === "GET")) {
        return json({
          state: "completed",
          guide_agent_id: "chulan",
          conversation_id: null,
          mission_id: null,
          first_reply_completed_at: null,
          completed_at: "2026-10-15T00:00:00Z",
          verified_provider_ids: [],
          providers: [],
          dismissed_real_cli_setup: true,
        });
      }
      if (url.startsWith("/api/conversations?") && (!init || init.method !== "POST")) {
        return json(config.conversations ?? []);
      }
      if (url === "/api/conversations" && init?.method === "POST") {
        return json({
          id: "c_1",
          title: "新对话",
          status: "active",
          created_at: "2026-10-15T00:00:00Z",
          updated_at: "2026-10-15T00:00:00Z",
        });
      }
      if (isMissionCreate(url, init)) {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        if (config.missionCreate) return config.missionCreate(body);
        return json(makeConversationDetail());
      }
      if (/\/api\/missions\/[^/]+\/interruptions$/.test(url) && init?.method === "POST") {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        if (config.interrupt) return config.interrupt(body);
        return json(roundtableMission({ turnsUsed: 2, phase: "exchange" }));
      }
      if (/\/api\/missions\/[^/]+\/discussion-controls$/.test(url) && init?.method === "POST") {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        if (config.control) return config.control(body);
        return json(roundtableMission({ phase: "closing", pendingControl: "finish" }));
      }
      if (/\/api\/conversations\/[^/]+$/.test(url) && (!init || init.method === "GET")) {
        const detail = config.conversationDetail;
        if (typeof detail === "function") return json(detail());
        return json(detail ?? makeConversationDetail());
      }
      return new Response("{}", { status: 404 });
    }),
  );
  return calls;
}

async function openRoundtableMode(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("radio", { name: "多人讨论" }));
}

async function typeGoal(user: ReturnType<typeof userEvent.setup>, goal = GOAL) {
  await user.type(screen.getByLabelText("任务输入"), goal);
}

function lastBody(
  calls: Array<{ url: string; init?: RequestInit }>,
  matcher: (url: string, init?: RequestInit) => boolean,
): Record<string, unknown> | undefined {
  const call = [...calls].reverse().find((entry) => matcher(entry.url, entry.init));
  return call ? (JSON.parse(String(call.init?.body)) as Record<string, unknown>) : undefined;
}

describe("AgentClub roundtable UI", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts a three-person discussion with the roundtable payload", async () => {
    const user = userEvent.setup();
    const calls = stubRoundtableFetch();
    render(<App />);

    await openRoundtableMode(user);
    expect(screen.getByText("通透机敏")).toBeInTheDocument();
    expect(screen.getByText("坦率好奇")).toBeInTheDocument();
    expect(screen.getByText("松弛笃定")).toBeInTheDocument();
    await typeGoal(user);
    await user.click(screen.getByRole("button", { name: "开始讨论" }));

    const body = lastBody(calls, isMissionCreate);
    expect(body?.goal).toBe(GOAL);
    expect(body?.room_mode).toBe("roundtable");
    expect(body?.discussion).toEqual({ participant_ids: ALL_PARTICIPANTS });
    expect(String(body?.request_id)).toMatch(/^start-/);
    expect(body).not.toHaveProperty("target_agent_ids");

    expect(await screen.findByText("初次交流")).toBeInTheDocument();
    expect(screen.getByText("已用 1 / 最多 7 个回合（含总结）")).toBeInTheDocument();
    expect(screen.getByText("参与者：张楚岚、冯宝宝、王也")).toBeInTheDocument();
  });

  it("starts a two-person discussion with the smaller default budget", async () => {
    const user = userEvent.setup();
    const calls = stubRoundtableFetch({
      missionCreate: () =>
        json(
          makeConversationDetail({
            latest_mission_detail: roundtableMission({
              participants: ["chulan", "baobao"],
              turnsLimit: 5,
            }),
          }),
        ),
    });
    render(<App />);

    await openRoundtableMode(user);
    await user.click(screen.getByRole("checkbox", { name: /王也/ }));
    expect(screen.getByText(/默认最多 5 个回合（含总结）/)).toBeInTheDocument();
    await typeGoal(user);
    await user.click(screen.getByRole("button", { name: "开始讨论" }));

    expect(lastBody(calls, isMissionCreate)?.discussion).toEqual({
      participant_ids: ["chulan", "baobao"],
    });
    expect(await screen.findByText("已用 1 / 最多 5 个回合（含总结）")).toBeInTheDocument();
    expect(screen.getByText("参与者：张楚岚、冯宝宝")).toBeInTheDocument();
  });

  it("refuses to start with fewer than two participants", async () => {
    const user = userEvent.setup();
    const calls = stubRoundtableFetch();
    render(<App />);

    await openRoundtableMode(user);
    await user.click(screen.getByRole("checkbox", { name: /王也/ }));
    await user.click(screen.getByRole("checkbox", { name: /冯宝宝/ }));
    await typeGoal(user);

    expect(screen.getByRole("alert")).toHaveTextContent("至少要 2 位参与者");
    expect(screen.getByRole("button", { name: "开始讨论" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "开始讨论" }));
    expect(calls.some((call) => isMissionCreate(call.url, call.init))).toBe(false);
  });

  it("locks the mode and the participant set while a discussion runs", async () => {
    const user = userEvent.setup();
    stubRoundtableFetch();
    render(<App />);

    await openRoundtableMode(user);
    await typeGoal(user);
    await user.click(screen.getByRole("button", { name: "开始讨论" }));
    await screen.findByRole("button", { name: "发送插话" });

    expect(screen.getByRole("radio", { name: "任务" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "多人讨论" })).toBeDisabled();
    const participants = screen.getByRole("list", { name: "参与者" });
    within(participants)
      .getAllByRole("checkbox")
      .forEach((box) => expect(box).toBeDisabled());
    expect(screen.getByRole("status")).toHaveTextContent("模式和参与者已锁定");
  });

  it("sends an interruption to the picked participant with an idempotency key", async () => {
    const user = userEvent.setup();
    const calls = stubRoundtableFetch();
    render(<App />);

    await openRoundtableMode(user);
    await typeGoal(user);
    await user.click(screen.getByRole("button", { name: "开始讨论" }));
    await screen.findByRole("button", { name: "发送插话" });
    await waitFor(() => expect(screen.getByLabelText("任务输入")).toHaveValue(""));

    await user.type(screen.getByLabelText("任务输入"), "@王也 只有一周开发时间");
    expect(screen.queryByRole("listbox", { name: "Agent 候选" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("点名先回应（可选）"), "wangye");
    await user.click(screen.getByRole("button", { name: "发送插话" }));

    const body = lastBody(
      calls,
      (url, init) => /\/api\/missions\/m1\/interruptions$/.test(url) && init?.method === "POST",
    );
    expect(body?.content).toBe("@王也 只有一周开发时间");
    expect(body?.target_agent_id).toBe("wangye");
    expect(String(body?.request_id)).toMatch(/^input-/);
    expect(await screen.findByText("继续讨论")).toBeInTheDocument();
  });

  it("asks to wrap up after the current turn without claiming an immediate stop", async () => {
    const user = userEvent.setup();
    const calls = stubRoundtableFetch();
    render(<App />);

    await openRoundtableMode(user);
    await typeGoal(user);
    await user.click(screen.getByRole("button", { name: "开始讨论" }));
    await screen.findByRole("button", { name: "本轮后收尾" });

    await user.selectOptions(screen.getByLabelText("收尾人（可选）"), "baobao");
    await user.click(screen.getByRole("button", { name: "本轮后收尾" }));

    const body = lastBody(
      calls,
      (url, init) => /\/api\/missions\/m1\/discussion-controls$/.test(url) && init?.method === "POST",
    );
    expect(body?.action).toBe("finish");
    expect(body?.summary_agent_id).toBe("baobao");
    expect(String(body?.request_id)).toMatch(/^finish-/);

    const pending = await screen.findByText(/已请求收尾/);
    expect(pending).toHaveTextContent("等当前回合结束后开始总结");
    expect(pending).toHaveTextContent("不会立即停止 CLI");
    expect(screen.getByRole("button", { name: "本轮后收尾" })).toBeDisabled();
  });

  it("asks to stop the discussion after the current turn", async () => {
    const user = userEvent.setup();
    const calls = stubRoundtableFetch({
      control: () => json(roundtableMission({ phase: "closing", pendingControl: "stop" })),
    });
    render(<App />);

    await openRoundtableMode(user);
    await typeGoal(user);
    await user.click(screen.getByRole("button", { name: "开始讨论" }));
    await screen.findByRole("button", { name: "本轮后停止" });
    await user.click(screen.getByRole("button", { name: "本轮后停止" }));

    const body = lastBody(
      calls,
      (url, init) => /\/api\/missions\/m1\/discussion-controls$/.test(url) && init?.method === "POST",
    );
    expect(body?.action).toBe("stop");
    expect(String(body?.request_id)).toMatch(/^stop-/);
    expect(await screen.findByText(/已请求停止/)).toHaveTextContent("等当前回合结束后结束讨论");
  });

  it("restores a running discussion from the API and follows SSE transitions", async () => {
    const user = userEvent.setup();
    stubRoundtableFetch({
      conversations: [conversationSummary()],
      conversationDetail: () =>
        makeConversationDetail({
          latest_mission_detail: roundtableMission({
            phase: "closing",
            turnsUsed: 5,
            pendingControl: "finish",
          }),
        }),
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /复盘会/ }));

    expect(await screen.findByText("正在收尾")).toBeInTheDocument();
    expect(screen.getByText("已用 5 / 最多 7 个回合（含总结）")).toBeInTheDocument();
    expect(screen.getByText(/已请求收尾/)).toBeInTheDocument();

    const EventSourceMock = window.EventSource as unknown as {
      instances: Array<EventTarget & { close: ReturnType<typeof vi.fn> }>;
    };
    const source = EventSourceMock.instances[0];
    expect(source).toBeDefined();
    source.dispatchEvent(
      new MessageEvent("snapshot", {
        data: JSON.stringify({
          ...roundtableMission({
            phase: "ended",
            turnsUsed: 7,
            endReason: "user_finish",
            status: "completed",
            summaryMessageIds: ["msg_summary"],
          }),
        }),
      }),
    );

    expect(await screen.findByText("已结束")).toBeInTheDocument();
    expect(screen.getByText("结束原因：你要求收尾 · 总结消息 1 条")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续讨论" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("radio", { name: "任务" })).not.toBeDisabled());
  });

  it("continues a finished discussion with the same participants", async () => {
    const user = userEvent.setup();
    const calls = stubRoundtableFetch({
      conversations: [conversationSummary()],
      conversationDetail: () =>
        makeConversationDetail({
          latest_mission_detail: roundtableMission({
            phase: "ended",
            status: "completed",
            turnsUsed: 5,
            turnsLimit: 5,
            participants: ["chulan", "baobao"],
            endReason: "budget_exhausted",
          }),
        }),
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /复盘会/ }));
    expect(await screen.findByText("结束原因：回合预算用尽，正常结束")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "继续讨论" }));
    expect(screen.getByRole("radio", { name: "多人讨论" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByLabelText("任务输入")).toHaveValue(GOAL);
    expect(screen.getByRole("checkbox", { name: /王也/ })).not.toBeChecked();

    const nextGoal = "只保留一周内能做完的事";
    await user.clear(screen.getByLabelText("任务输入"));
    await user.type(screen.getByLabelText("任务输入"), nextGoal);
    await user.click(screen.getByRole("button", { name: "开始讨论" }));

    const body = lastBody(calls, isMissionCreate);
    expect(body?.goal).toBe(nextGoal);
    expect(body?.discussion).toEqual({ participant_ids: ["chulan", "baobao"] });
  });

  it("keeps the interrupted text and explains a rejected interruption", async () => {
    const user = userEvent.setup();
    const calls = stubRoundtableFetch({
      interrupt: () =>
        json(
          { detail: "Discussion is closing or stopping; save this input for the next discussion" },
          409,
        ),
    });
    render(<App />);

    await openRoundtableMode(user);
    await typeGoal(user);
    await user.click(screen.getByRole("button", { name: "开始讨论" }));
    await screen.findByRole("button", { name: "发送插话" });
    await waitFor(() => expect(screen.getByLabelText("任务输入")).toHaveValue(""));

    await user.type(screen.getByLabelText("任务输入"), "预算砍半");
    await user.click(screen.getByRole("button", { name: "发送插话" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("输入仍保留");
    expect(screen.getByLabelText("任务输入")).toHaveValue("预算砍半");
    await waitFor(() =>
      expect(calls.some((call) => /\/api\/conversations\/c_1$/.test(call.url))).toBe(true),
    );
  });

  it("reports a provider failure ending", async () => {
    const user = userEvent.setup();
    stubRoundtableFetch({
      conversations: [conversationSummary()],
      conversationDetail: () =>
        makeConversationDetail({
          latest_mission_detail: roundtableMission({
            phase: "ended",
            status: "blocked",
            endReason: "provider_failed",
          }),
        }),
    });
    render(<App />);

    await user.click(await screen.findByRole("button", { name: /复盘会/ }));
    expect(await screen.findByText("结束原因：Provider 失败")).toBeInTheDocument();
    expect(screen.getByText("任务状态：已阻塞")).toBeInTheDocument();
  });

  it("keeps task mode as the default for older conversations", async () => {
    const user = userEvent.setup();
    const calls = stubRoundtableFetch({
      missionCreate: () =>
        json(
          makeConversationDetail({
            latest_mission_detail: {
              mission: {
                id: "m_task",
                goal: "普通任务",
                status: "completed" as MissionStatus,
                created_at: "2026-10-15T00:00:00Z",
                updated_at: "2026-10-15T00:01:00Z",
              },
              messages: [],
              events: [],
              artifacts: [],
            },
          }),
        ),
    });
    render(<App />);

    expect(screen.getByRole("radio", { name: "任务" })).toHaveAttribute("aria-checked", "true");
    await typeGoal(user, "普通任务");
    await user.click(screen.getByRole("button", { name: "创建任务" }));

    const body = lastBody(calls, isMissionCreate);
    expect(body).toEqual({ goal: "普通任务" });
    expect(screen.queryByText(/个回合（含总结）/)).not.toBeInTheDocument();
  });
});
