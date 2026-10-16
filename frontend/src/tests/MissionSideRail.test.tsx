import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MissionSideRail } from "../components/MissionSideRail";
import type { MissionDetail, ToolEventRecord, TurnRecord } from "../types";

const stamp = "2026-04-30T01:00:00Z";
const status = { providers: [], loading: false, error: null, refresh: vi.fn() };

function turn(turn_id: string, agent_id: string): TurnRecord {
  return {
    turn_id, agent_id, provider_id: "fake", status: "completed",
    attempts: [], message_ids: [],
  };
}

function event(id: number, event_type: ToolEventRecord["event_type"], tool_name: string, turn_id: string): ToolEventRecord {
  return { id, mission_id: "m1", event_type, tool_name, turn_id,
    agent_id: turn_id === "turn_2" ? "baobao" : "chulan", created_at: stamp };
}

function detail(): MissionDetail {
  return {
    mission: { id: "m1", goal: "g", title: "t", status: "running", created_at: stamp, updated_at: stamp },
    turns: [turn("turn_1", "chulan"), turn("turn_2", "baobao"), turn("turn_3", "chulan")],
    tool_events: [
      { ...event(1, "tool.call", "read_context", "turn_1"), tool_input: { query: "brief" } },
      { ...event(2, "tool.result", "read_context", "turn_1"), summary: "读取完成" },
      event(3, "tool.call", "invoke_agent", "turn_1"),
      event(4, "tool.result", "invoke_agent", "turn_1"),
      event(5, "tool.call", "write_artifact", "turn_2"),
      { ...event(6, "tool.error", "write_artifact", "turn_2"), error: "写入失败" },
    ],
    a2a_invocations: [{ event_id: 7, sender_agent_id: "chulan", sender_turn_id: "turn_1",
      target_agent_id: "baobao", target_turn_id: "turn_2", purpose: "检查实现", mode: "wait_reply",
      created_at: stamp, reply_text: "已经检查" }],
    artifacts: [{ id: "a1", mission_id: "m1", title: "summary.md", kind: "md", path: "summary.md", created_at: stamp }],
  };
}

function renderRail(data = detail()) {
  return render(<MissionSideRail detail={data} providerStatus={status} />);
}

describe("MissionSideRail trace dialog", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows only the launcher in the right rail, then opens a live timeline", () => {
    renderRail();
    const rail = screen.getByRole("complementary", { name: "任务侧栏" });
    expect(within(rail).getAllByRole("button")).toHaveLength(1);
    expect(within(rail).queryByText("A2A 路由")).not.toBeInTheDocument();
    expect(within(rail).queryByText("阻塞 / 错误")).not.toBeInTheDocument();
    expect(within(rail).getByText("3 次调用")).toBeInTheDocument();

    fireEvent.click(within(rail).getByRole("button", { name: "打开工具与协作追踪" }));
    const dialog = screen.getByRole("dialog", { name: "工具与协作追踪" });
    expect(within(dialog).getByText("3 次工具调用")).toBeInTheDocument();
    expect(within(dialog).getByText("3 轮")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /张楚岚委派给冯宝宝/ })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /冯宝宝回复张楚岚/ })).toBeInTheDocument();
  });

  it("inspects tool input, error and the exact A2A request and reply", () => {
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: "打开工具与协作追踪" }));
    const dialog = screen.getByRole("dialog", { name: "工具与协作追踪" });
    fireEvent.click(within(dialog).getByRole("button", { name: /01 read_context/ }));
    expect(within(dialog).getByText(/"query": "brief"/)).toBeInTheDocument();
    expect(within(dialog).getByText("读取完成")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: /01 write_artifact/ }));
    expect(within(dialog).getByText("写入失败")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /张楚岚委派给冯宝宝/ }));
    expect(within(dialog).getByText("请求：检查实现")).toBeInTheDocument();
    expect(within(dialog).getByText("回复：已经检查")).toBeInTheDocument();
  });

  it("keeps artifacts accessible and closes with Escape", async () => {
    renderRail();
    const launcher = screen.getByRole("button", { name: "打开工具与协作追踪" });
    fireEvent.click(launcher);
    const dialog = screen.getByRole("dialog", { name: "工具与协作追踪" });
    fireEvent.click(within(dialog).getByText("产物", { selector: "summary" }));
    expect(within(dialog).getByText("summary.md")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /summary.md/ }));
    expect(screen.getByRole("dialog", { name: "summary.md" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "summary.md" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "工具与协作追踪" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "工具与协作追踪" })).not.toBeInTheDocument();
    await waitFor(() => expect(launcher).toHaveFocus());
  });

  it("handles a mission without turns", () => {
    renderRail({ ...detail(), turns: [], tool_events: [], a2a_invocations: [] });
    fireEvent.click(screen.getByRole("button", { name: "打开工具与协作追踪" }));
    expect(screen.getByText(/暂无轮次/)).toBeInTheDocument();
  });

  it("keeps an unmatched tool error visible without inflating call count", () => {
    const data = detail();
    data.tool_events = [{ ...event(30, "tool.error", "invoke_agent", "turn_1"), error: "目标不可用" }];
    renderRail(data);
    expect(screen.getByText("0 次调用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开工具与协作追踪" }));
    const dialog = screen.getByRole("dialog", { name: "工具与协作追踪" });
    fireEvent.click(within(dialog).getByRole("button", { name: /invoke_agent.*失败/ }));
    expect(within(dialog).getByText("目标不可用")).toBeInTheDocument();
  });
});
