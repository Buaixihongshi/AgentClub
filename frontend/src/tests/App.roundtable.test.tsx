import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { ConversationDetail, ConversationSummary, DiscussionView, MissionDetail, MissionStatus } from "../types";

const GOAL = "Club 下一步该做什么？";
const STAMP = "2026-10-20T00:00:00Z";
const ALL_PARTICIPANTS = ["chulan", "baobao", "wangye"];
type RecordedCall = { url: string; init?: RequestInit };

function json(payload: unknown, status = 200): Response { return new Response(JSON.stringify(payload), { status }); }
function isMissionCreate(url: string, init?: RequestInit): boolean { return /\/api\/conversations\/[^/]+\/missions$/.test(url) && init?.method === "POST"; }
function isInterrupt(url: string, init?: RequestInit): boolean { return /\/api\/missions\/[^/]+\/interruptions$/.test(url) && init?.method === "POST"; }
function isControl(url: string, init?: RequestInit): boolean { return /\/api\/missions\/[^/]+\/discussion-controls$/.test(url) && init?.method === "POST"; }

function skillMission(discussion: Partial<DiscussionView> = {}, status: MissionStatus = "running"): MissionDetail {
  return {
    mission: { id: "m1", goal: GOAL, room_mode: "roundtable", discussion: { protocol: "deliberation_v2", participant_ids: ALL_PARTICIPANTS, max_turns: 13, max_exchange_rounds: 2 }, status, created_at: STAMP, updated_at: STAMP },
    discussion: { protocol: "deliberation_v2", participant_ids: ALL_PARTICIPANTS, phase: "independent", turns_used: 3, turns_limit: 13, revision: 7, results: [], ...discussion },
    messages: [], events: [], artifacts: [],
  };
}
function ordinaryMission(goal = "普通任务"): MissionDetail {
  return { mission: { id: "m_task", goal, status: "completed", created_at: STAMP, updated_at: STAMP }, messages: [], events: [], artifacts: [] };
}
function conversationDetail(detail = skillMission()): ConversationDetail {
  return { conversation: { id: "c_1", title: "复盘会", status: "active", created_at: STAMP, updated_at: STAMP }, missions: [detail.mission], messages: detail.messages ?? [], latest_mission_detail: detail };
}
function conversationSummary(): ConversationSummary {
  return { id: "c_1", title: "复盘会", status: "active", created_at: STAMP, updated_at: STAMP, mission_count: 1, latest_mission_id: "m1" };
}
interface StubConfig {
  conversations?: ConversationSummary[];
  conversation?: () => ConversationDetail;
  health?: Record<string, unknown>;
  missionCreate?: (body: Record<string, unknown>) => Response;
  interrupt?: (body: Record<string, unknown>) => Response;
  control?: (body: Record<string, unknown>) => Response;
}
function stubSkillFetch(config: StubConfig = {}) {
  const calls: RecordedCall[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); calls.push({ url, init });
    if (url === "/api/providers") return json([]);
    if (url === "/api/agents") return json([
      { id: "chulan", display_name: "张楚岚", aliases: ["楚岚"], role: "架构与开发" },
      { id: "baobao", display_name: "冯宝宝", aliases: ["宝宝"], role: "代码审查" },
      { id: "wangye", display_name: "王也", aliases: [], role: "视觉设计" },
    ]);
    if (url === "/api/health") return json({ status: "ok", version: "0.1.0", database: "ready", config: "ready", fake_provider: "ready", discussion_protocols: ["roundtable_v1", "deliberation_v2"], project_skills: ["discuss"], ...config.health });
    if (url === "/api/onboarding" && (!init || init.method === "GET")) return json({ state: "completed", guide_agent_id: "chulan", conversation_id: null, mission_id: null, first_reply_completed_at: null, completed_at: STAMP, verified_provider_ids: [], providers: [], dismissed_real_cli_setup: true });
    if (url.startsWith("/api/conversations?") && (!init || init.method !== "POST")) return json(config.conversations ?? []);
    if (url === "/api/conversations" && init?.method === "POST") return json({ id: "c_1", title: "新对话", status: "active", created_at: STAMP, updated_at: STAMP });
    if (isMissionCreate(url, init)) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return config.missionCreate?.(body) ?? json(conversationDetail(body.skill === "discuss" ? skillMission() : ordinaryMission(String(body.goal))));
    }
    if (isInterrupt(url, init)) return config.interrupt?.(JSON.parse(String(init?.body))) ?? json(skillMission({ phase: "exchange", revision: 8 }));
    if (isControl(url, init)) return config.control?.(JSON.parse(String(init?.body))) ?? json(skillMission({ phase: "synthesis", revision: 8 }));
    if (/\/api\/conversations\/[^/]+$/.test(url) && (!init || init.method === "GET")) return json(config.conversation?.() ?? conversationDetail());
    return json({}, 404);
  }));
  return calls;
}
function lastBody(calls: RecordedCall[], matcher: (url: string, init?: RequestInit) => boolean): Record<string, unknown> | undefined {
  const call = [...calls].reverse().find((entry) => matcher(entry.url, entry.init));
  return call ? JSON.parse(String(call.init?.body)) : undefined;
}
async function submitText(user: ReturnType<typeof userEvent.setup>, value: string, button = "发送") {
  await user.type(screen.getByLabelText("消息输入"), value);
  await user.click(screen.getByRole("button", { name: button }));
}

describe("discussion as a project skill in chat", () => {
  beforeEach(() => { vi.unstubAllGlobals(); });

  it("invokes /discuss through the project skill without exposing orchestration setup", async () => {
    const user = userEvent.setup(); const calls = stubSkillFetch(); render(<App />);
    expect(screen.queryByRole("radio", { name: "多人讨论" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "任务" })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "参与者" })).not.toBeInTheDocument();
    await submitText(user, `/discuss ${GOAL}`);
    expect(lastBody(calls, isMissionCreate)).toEqual({ goal: GOAL, skill: "discuss", request_id: expect.stringMatching(/^skill-/) });
    expect(await screen.findByText("团队正在形成独立判断…")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "讨论重点" })).toHaveTextContent(GOAL);
    expect(screen.queryByRole("list", { name: "讨论流程" })).not.toBeInTheDocument();
    expect(screen.queryByText(/已用 .*最多|参与者：|六阶段讨论/)).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /王也/ })).not.toBeInTheDocument();
  });

  it("keeps an empty /discuss invocation in the composer and sends no request", async () => {
    const user = userEvent.setup(); const calls = stubSkillFetch(); render(<App />);
    await submitText(user, "/discuss");
    expect(await screen.findByRole("alert")).toHaveTextContent(/议题|问题|内容/);
    expect(screen.getByLabelText("消息输入")).toHaveValue("/discuss");
    expect(calls.some((call) => isMissionCreate(call.url, call.init))).toBe(false);
    expect(calls.some((call) => call.url === "/api/conversations" && call.init?.method === "POST")).toBe(false);
  });

  it("treats /discussfoo as ordinary text rather than a skill command", async () => {
    const user = userEvent.setup(); const calls = stubSkillFetch(); render(<App />);
    await submitText(user, "/discussfoo 请保留原文");
    expect(lastBody(calls, isMissionCreate)).toEqual({ goal: "/discussfoo 请保留原文" });
  });

  it("keeps ordinary tasks as the default and preserves their request format", async () => {
    const user = userEvent.setup(); const calls = stubSkillFetch(); render(<App />);
    await submitText(user, "普通任务");
    expect(lastBody(calls, isMissionCreate)).toEqual({ goal: "普通任务" });
    expect(screen.queryByRole("region", { name: "讨论技能" })).not.toBeInTheDocument();
  });

  it.each([{ project_skills: [] }, { discussion_protocols: ["roundtable_v1"] }])("preserves the command when the backend lacks the skill/protocol: %j", async (health) => {
    const user = userEvent.setup(); const calls = stubSkillFetch({ health }); render(<App />);
    await submitText(user, `/discuss ${GOAL}`);
    expect(await screen.findByRole("alert")).toHaveTextContent(/后端|重启|技能|Skill/i);
    expect(screen.getByLabelText("消息输入")).toHaveValue(`/discuss ${GOAL}`);
    expect(calls.some((call) => isMissionCreate(call.url, call.init))).toBe(false);
    expect(calls.some((call) => call.url === "/api/conversations" && call.init?.method === "POST")).toBe(false);
  });

  it("submits active skill conditions without routing to a named participant", async () => {
    const user = userEvent.setup(); const calls = stubSkillFetch(); render(<App />);
    await submitText(user, `/discuss ${GOAL}`);
    await screen.findByRole("button", { name: "发送插话" });
    await waitFor(() => expect(screen.getByLabelText("消息输入")).toHaveValue(""));
    expect(screen.queryByLabelText("点名先回应（可选）")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("消息输入"), "@王也 只有一周开发时间");
    expect(screen.queryByRole("listbox", { name: "Agent 候选" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "发送插话" }));
    const body = lastBody(calls, isInterrupt);
    expect(body?.content).toBe("@王也 只有一周开发时间");
    expect(body).not.toHaveProperty("target_agent_id");
    expect(String(body?.request_id)).toMatch(/^input-/);
  });

  it("sends an explicit synthesizer selection with the current state revision", async () => {
    const user = userEvent.setup();
    const waiting = skillMission({ phase: "awaiting_synthesizer", awaiting_reason: "select_synthesizer", revision: 12 }, "awaiting_input");
    const calls = stubSkillFetch({ conversations: [conversationSummary()], conversation: () => conversationDetail(waiting) }); render(<App />);
    await user.click(await screen.findByRole("button", { name: /复盘会/ }));
    const select = await screen.findByLabelText("负责综合的伙伴");
    expect(select).toHaveValue("");
    expect(screen.getByRole("button", { name: "由其综合" })).toBeDisabled();
    expect(calls.some((call) => isControl(call.url, call.init))).toBe(false);
    await user.selectOptions(select, "wangye");
    await user.click(screen.getByRole("button", { name: "由其综合" }));
    expect(lastBody(calls, isControl)).toEqual({ action: "select_synthesizer", agent_id: "wangye", expected_revision: 12, request_id: expect.stringMatching(/^select_synthesizer-/) });
  });

  it("follows skill completion over SSE without making the next ordinary message a discussion", async () => {
    const user = userEvent.setup(); let current = skillMission();
    const calls = stubSkillFetch({ conversations: [conversationSummary()], conversation: () => conversationDetail(current) }); render(<App />);
    await user.click(await screen.findByRole("button", { name: /复盘会/ }));
    await screen.findByText("团队正在形成独立判断…");
    const EventSourceMock = window.EventSource as unknown as { instances: Array<EventTarget & { close: ReturnType<typeof vi.fn> }> };
    await waitFor(() => expect(EventSourceMock.instances.length).toBeGreaterThan(0));
    current = skillMission({ phase: "ended", end_reason: "completed", revision: 19 }, "completed");
    EventSourceMock.instances[0].dispatchEvent(new MessageEvent("snapshot", { data: JSON.stringify(current) }));
    expect(await screen.findByText("团队讨论已完成")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发送插话" })).not.toBeInTheDocument();
    await submitText(user, "帮我整理README");
    expect(lastBody(calls, isMissionCreate)).toEqual({ goal: "帮我整理README" });
  });

  it("keeps rejected interruption text and refreshes the conversation after a 409", async () => {
    const user = userEvent.setup();
    const calls = stubSkillFetch({ interrupt: () => json({ detail: "Discussion is closing or stopping; save this input for the next discussion" }, 409) }); render(<App />);
    await submitText(user, `/discuss ${GOAL}`);
    await screen.findByRole("button", { name: "发送插话" });
    await waitFor(() => expect(screen.getByLabelText("消息输入")).toHaveValue(""));
    await submitText(user, "预算砍半", "发送插话");
    expect(await screen.findByRole("alert")).toHaveTextContent("输入仍保留");
    expect(screen.getByLabelText("消息输入")).toHaveValue("预算砍半");
    await waitFor(() => expect(calls.some((call) => call.url === "/api/conversations/c_1" && (!call.init || call.init.method === "GET"))).toBe(true));
  });

  it("keeps the original skill command when creation loses a 409 race", async () => {
    const user = userEvent.setup();
    const calls = stubSkillFetch({ missionCreate: () => json({ detail: "Conversation already has an active mission" }, 409) }); render(<App />);
    await submitText(user, `/discuss ${GOAL}`);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByLabelText("消息输入")).toHaveValue(`/discuss ${GOAL}`);
    expect(calls.filter((call) => isMissionCreate(call.url, call.init))).toHaveLength(1);
  });

  it("retains lightweight finish and stop controls for old V1 discussions", async () => {
    const user = userEvent.setup(); const legacy = skillMission({ protocol: "roundtable_v1", phase: "exchange" });
    const calls = stubSkillFetch({ conversations: [conversationSummary()], conversation: () => conversationDetail(legacy), control: () => json(legacy) }); render(<App />);
    await user.click(await screen.findByRole("button", { name: /复盘会/ }));
    await user.click(await screen.findByRole("button", { name: "整理结果" }));
    expect(lastBody(calls, isControl)).toEqual({ action: "finish", request_id: expect.stringMatching(/^finish-/) });
    await user.click(screen.getByRole("button", { name: "停止讨论" }));
    expect(lastBody(calls, isControl)).toEqual({ action: "stop", request_id: expect.stringMatching(/^stop-/) });
    expect(screen.queryByRole("list", { name: "讨论流程" })).not.toBeInTheDocument();
  });
});
