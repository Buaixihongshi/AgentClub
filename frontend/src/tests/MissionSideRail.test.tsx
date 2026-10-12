import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MissionSideRail } from "../components/MissionSideRail";
import type { MissionDetail } from "../types";

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
  artifacts: [
    {
      id: "a1",
      mission_id: "m1",
      title: "summary.md",
      kind: "md",
      path: "summary.md",
      created_at: "2026-04-30T01:03:00Z",
    },
  ],
  turns: [
    {
      turn_id: "turn_1",
      agent_id: "chulan",
      provider_id: "claude",
      status: "completed",
      attempts: [
        { attempt: 1, provider_id: "claude", event_type: "turn.completed", duration_ms: 120 },
      ],
      started_at: "2026-04-30T01:01:00Z",
      completed_at: "2026-04-30T01:01:01Z",
      duration_ms: 120,
      message_ids: ["msg_1"],
    },
    {
      turn_id: "turn_2",
      agent_id: "wangye",
      provider_id: "gemini",
      status: "completed",
      attempts: [
        { attempt: 1, provider_id: "gemini", event_type: "turn.blocked", error: "quota" },
        { attempt: 2, provider_id: "codex", event_type: "turn.completed", duration_ms: 240 },
      ],
      started_at: "2026-04-30T01:02:00Z",
      completed_at: "2026-04-30T01:02:02Z",
      duration_ms: 240,
      message_ids: ["msg_2"],
    },
  ],
  tool_events: [
    {
      id: 1,
      mission_id: "m1",
      event_type: "tool.call",
      tool_name: "write_artifact",
      agent_id: "baobao",
      turn_id: "turn_2",
      provider_id: "codex",
      created_at: "2026-04-30T01:02:01Z",
    },
    {
      id: 2,
      mission_id: "m1",
      event_type: "tool.result",
      tool_name: "write_artifact",
      agent_id: "baobao",
      turn_id: "turn_2",
      provider_id: "codex",
      artifact_id: "m1:summary.md",
      summary: "Wrote summary.md",
      created_at: "2026-04-30T01:02:02Z",
    },
  ],
  interrupts: [
    {
      event_id: 12,
      content: "这个先处理完再继续",
      target_agent_id: "wangye",
      status: "handled",
      created_at: "2026-04-30T01:01:30Z",
      handled_by_agent_id: "wangye",
      handled_at: "2026-04-30T01:02:02Z",
      turn_id: "turn_2",
    },
    {
      event_id: 13,
      content: "等我确认",
      target_agent_id: "baobao",
      status: "pending",
      created_at: "2026-04-30T01:03:00Z",
    },
  ],
};

describe("MissionSideRail", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders mission status and artifacts", () => {
    render(
      <MissionSideRail
        detail={detail}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    expect(screen.getByRole("complementary", { name: "任务侧栏" })).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByText("复杂度：complex")).toBeInTheDocument();
    expect(screen.getByText("summary.md")).toBeInTheDocument();
  });

  it("renders provider issues and empty artifact state", () => {
    render(
      <MissionSideRail
        detail={{ ...detail, artifacts: [] }}
        providerStatus={{
          providers: [
            { id: "claude", label: "Claude Code", enabled: true, available: false, error: "429 quota" },
          ],
          loading: false,
          error: null,
          refresh: vi.fn(),
        }}
      />,
    );

    const blockerPanel = screen.getByRole("region", { name: "阻塞和错误" });
    expect(screen.getByText("暂无产物")).toBeInTheDocument();
    expect(within(blockerPanel).getByText("Claude Code：429 quota")).toBeInTheDocument();
  });

  it("renders latest turn progress when turn records exist", () => {
    render(
      <MissionSideRail
        detail={detail}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    const turnPanel = screen.getByRole("region", { name: "轮次追踪" });
    expect(within(turnPanel).getAllByText("turn_2").length).toBeGreaterThan(0);
    expect(within(turnPanel).getByText("Agent：wangye")).toBeInTheDocument();
    expect(within(turnPanel).getByText("Provider：Gemini")).toBeInTheDocument();
    expect(within(turnPanel).getByText("状态：已完成")).toBeInTheDocument();
    expect(within(turnPanel).getByText("尝试：2")).toBeInTheDocument();
    expect(within(turnPanel).getByText("耗时：240ms")).toBeInTheDocument();
  });

  it("renders provider fallback details for multi-attempt turns", () => {
    render(
      <MissionSideRail
        detail={detail}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    const turnPanel = screen.getByRole("region", { name: "轮次追踪" });
    expect(within(turnPanel).getByText("默认 Provider 失败，已切换备用 Provider")).toBeInTheDocument();
    expect(within(turnPanel).getByText("Gemini：已阻塞")).toBeInTheDocument();
    expect(within(turnPanel).getByText("Codex：已完成")).toBeInTheDocument();
  });

  it("keeps provider fallback visible when it is not the latest turn", () => {
    render(
      <MissionSideRail
        detail={{
          ...detail,
          turns: [
            ...(detail.turns ?? []),
            {
              turn_id: "turn_3",
              agent_id: "chulan",
              provider_id: "claude",
              status: "completed",
              attempts: [
                { attempt: 1, provider_id: "claude", event_type: "turn.completed", duration_ms: 120 },
              ],
              started_at: "2026-04-30T01:03:00Z",
              completed_at: "2026-04-30T01:03:01Z",
              duration_ms: 120,
              message_ids: ["msg_3"],
            },
          ],
        }}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    const turnPanel = screen.getByRole("region", { name: "轮次追踪" });
    expect(within(turnPanel).getByText("turn_3")).toBeInTheDocument();
    expect(within(turnPanel).getByText("默认 Provider 失败，已切换备用 Provider")).toBeInTheDocument();
    expect(within(turnPanel).getByText("turn_2")).toBeInTheDocument();
  });

  it("renders turn empty state only when turn records field exists", () => {
    const { rerender } = render(
      <MissionSideRail
        detail={{ ...detail, turns: [] }}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    expect(screen.getByRole("region", { name: "轮次追踪" })).toBeInTheDocument();
    expect(screen.getByText("暂无轮次追踪")).toBeInTheDocument();

    rerender(
      <MissionSideRail
        detail={{ ...detail, turns: undefined }}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    expect(screen.queryByRole("region", { name: "轮次追踪" })).not.toBeInTheDocument();
  });

  it("renders tool trace as a collapsed disclosure when tool events exist", () => {
    const { container } = render(
      <MissionSideRail
        detail={detail}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    const toolPanel = screen.getByRole("region", { name: "工具追踪" });
    const disclosure = container.querySelector(".tool-trace-disclosure");
    expect(disclosure).toBeInstanceOf(HTMLDetailsElement);
    expect((disclosure as HTMLDetailsElement).open).toBe(false);
    expect(within(toolPanel).getByText("2 条")).toBeInTheDocument();

    fireEvent.click(within(toolPanel).getByText("工具追踪"));

    expect((disclosure as HTMLDetailsElement).open).toBe(true);
    expect(within(toolPanel).getAllByText("write_artifact")).toHaveLength(2);
    expect(within(toolPanel).getByText("tool.call · baobao · turn_2")).toBeInTheDocument();
    expect(within(toolPanel).getByText("Wrote summary.md")).toBeInTheDocument();
  });

  it("renders interrupt queue with pending interrupts first", () => {
    render(
      <MissionSideRail
        detail={detail}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    const interruptPanel = screen.getByRole("region", { name: "插话队列" });
    const items = within(interruptPanel).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("待处理");
    expect(items[0]).toHaveTextContent("等我确认");
    expect(items[1]).toHaveTextContent("已处理");
    expect(items[1]).toHaveTextContent("这个先处理完再继续");
  });

  it("does not render tool and interrupt panels for older backend payloads", () => {
    render(
      <MissionSideRail
        detail={{ ...detail, tool_events: undefined, interrupts: undefined }}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    expect(screen.queryByRole("region", { name: "工具追踪" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "插话队列" })).not.toBeInTheDocument();
  });

  it("shows which agent produced each artifact", () => {
    render(
      <MissionSideRail
        detail={{
          ...detail,
          artifacts: [
            {
              id: "a1",
              mission_id: "m1",
              title: "summary.md",
              kind: "md",
              path: "summary.md",
              created_at: "2026-04-30T01:03:00Z",
              agent_id: "baobao",
              turn_id: "turn_2",
            },
          ],
        }}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    const artifactPanel = screen.getByRole("region", { name: "产物" });
    expect(within(artifactPanel).getByText("summary.md")).toBeInTheDocument();
    expect(within(artifactPanel).getByText(/baobao/)).toBeInTheDocument();
  });

  it("shows the relative path separately when it differs from the filename", () => {
    render(
      <MissionSideRail
        detail={{
          ...detail,
          artifacts: [
            {
              id: "a1",
              mission_id: "m1",
              title: "plan.md",
              kind: "md",
              path: "docs/roadmap/plan.md",
              created_at: "2026-04-30T01:03:00Z",
              agent_id: "baobao",
              turn_id: "turn_2",
            },
          ],
        }}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    const artifactPanel = screen.getByRole("region", { name: "产物" });
    // Filename is the title; the full path is shown as its own subdued element.
    expect(within(artifactPanel).getByText("plan.md")).toBeInTheDocument();
    expect(within(artifactPanel).getByText("docs/roadmap/plan.md")).toBeInTheDocument();
  });

  it("opens artifact content in a modal viewer on click", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/artifacts/content")) {
        return new Response(
          JSON.stringify({
            path: "summary.md",
            content: "# 复盘标题\n这是正文段落。",
            kind: "md",
            total_chars: 12,
            truncated: false,
          }),
          { status: 200 },
        );
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MissionSideRail
        detail={detail}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    const artifactPanel = screen.getByRole("region", { name: "产物" });
    expect(within(artifactPanel).queryByText("这是正文段落。")).not.toBeInTheDocument();

    fireEvent.click(within(artifactPanel).getByRole("button", { name: /summary\.md/ }));

    const dialog = await screen.findByRole("dialog", { name: /summary\.md/ });
    expect(within(dialog).getByText("这是正文段落。")).toBeInTheDocument();
    expect(within(artifactPanel).queryByText("这是正文段落。")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/missions/m1/artifacts/content?path=summary.md"),
    );

    fireEvent.click(within(dialog).getByRole("button", { name: "关闭产物查看器" }));

    expect(screen.queryByRole("dialog", { name: /summary\.md/ })).not.toBeInTheDocument();
  });

  it("closes the artifact modal with Escape", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(
        JSON.stringify({
          path: "summary.md",
          content: "弹窗正文",
          kind: "md",
          total_chars: 4,
          truncated: false,
        }),
        { status: 200 },
      ),
    ));

    render(
      <MissionSideRail
        detail={detail}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /summary\.md/ }));
    expect(await screen.findByRole("dialog", { name: /summary\.md/ })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog", { name: /summary\.md/ })).not.toBeInTheDocument();
  });

  it("renders A2A route tasks as a collapsed disclosure", () => {
    const { container } = render(
      <MissionSideRail
        detail={{
          ...detail,
          a2a_invocations: [
            {
              event_id: 1,
              sender_agent_id: "chulan",
              sender_turn_id: "turn_1",
              target_agent_id: "wangye",
              purpose: "第一条接力",
              mode: "fire_and_forget",
              queued_position: 1,
              created_at: "2026-04-30T01:01:00Z",
            },
            {
              event_id: 2,
              sender_agent_id: "chulan",
              sender_turn_id: "turn_1",
              target_agent_id: "baobao",
              purpose: "第二条接力",
              mode: "fire_and_forget",
              queued_position: 2,
              created_at: "2026-04-30T01:01:01Z",
            },
            {
              event_id: 3,
              sender_agent_id: "baobao",
              sender_turn_id: "turn_2",
              target_agent_id: "wangye",
              purpose: "第三条接力",
              mode: "fire_and_forget",
              queued_position: 3,
              created_at: "2026-04-30T01:01:02Z",
            },
            {
              event_id: 4,
              sender_agent_id: "wangye",
              sender_turn_id: "turn_3",
              target_agent_id: "chulan",
              purpose: "第四条接力",
              mode: "fire_and_forget",
              queued_position: 4,
              created_at: "2026-04-30T01:01:03Z",
            },
          ],
        }}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    const routePanel = screen.getByRole("region", { name: "A2A 路由" });
    const disclosure = container.querySelector(".a2a-route-disclosure");
    expect(disclosure).toBeInstanceOf(HTMLDetailsElement);
    expect((disclosure as HTMLDetailsElement).open).toBe(false);
    expect(within(routePanel).getByText("4 / 8")).toBeInTheDocument();

    fireEvent.click(within(routePanel).getByText("A2A 路由"));

    expect((disclosure as HTMLDetailsElement).open).toBe(true);
    expect(within(routePanel).getByText("第一条接力")).toBeInTheDocument();
    expect(within(routePanel).getByText("第二条接力")).toBeInTheDocument();
    expect(within(routePanel).getByText("第三条接力")).toBeInTheDocument();
    expect(within(routePanel).getByText("第四条接力")).toBeInTheDocument();
  });

  it("shows a blocked target turn instead of a pending A2A handoff", () => {
    render(
      <MissionSideRail
        detail={{
          ...detail,
          turns: [
            ...(detail.turns ?? []),
            {
              turn_id: "turn_3_blocked",
              agent_id: "baobao",
              provider_id: "claude",
              status: "blocked",
              attempts: [{
                attempt: 1,
                provider_id: "claude",
                status: "blocked",
                summary: "idle timeout after 90s",
              }],
              message_ids: [],
            },
          ],
          a2a_invocations: [{
            event_id: 5,
            sender_agent_id: "chulan",
            sender_turn_id: "turn_1",
            target_agent_id: "baobao",
            target_turn_id: "turn_3_blocked",
            purpose: "继续修复",
            mode: "wait_reply",
            created_at: "2026-04-30T01:01:04Z",
          }],
        }}
        providerStatus={{ providers: [], loading: false, error: null, refresh: vi.fn() }}
      />,
    );

    const routePanel = screen.getByRole("region", { name: "A2A 路由" });
    expect(within(routePanel).queryByText("等待 1 条 wait_reply 回复")).not.toBeInTheDocument();
    fireEvent.click(within(routePanel).getByText("A2A 路由"));
    expect(within(routePanel).getByText("接力已阻塞")).toBeInTheDocument();
  });
});
