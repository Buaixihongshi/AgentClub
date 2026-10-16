import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DiscussionStatus } from "../components/DiscussionStatus";
import type { DiscussionView } from "../types";

function makeDiscussion(overrides?: Partial<DiscussionView>): DiscussionView {
  return {
    participant_ids: ["chulan", "baobao", "wangye"],
    phase: "exchange",
    turns_used: 4,
    turns_limit: 7,
    current_speaker: "wangye",
    current_turn_id: "turn_4",
    pending_control: null,
    end_reason: null,
    summary_message_ids: [],
    unanswered_interrupt_ids: [],
    ...overrides,
  };
}

function renderStatus(overrides?: {
  discussion?: Partial<DiscussionView>;
  missionStatus?: "running" | "completed" | "blocked" | "failed";
  busy?: boolean;
}) {
  const props = {
    discussion: makeDiscussion(overrides?.discussion),
    missionStatus: overrides?.missionStatus ?? ("running" as const),
    busy: overrides?.busy ?? false,
    onRequestFinish: vi.fn(),
    onRequestStop: vi.fn(),
    onContinue: vi.fn(),
  };
  return { ...render(<DiscussionStatus {...props} />), props };
}

describe("DiscussionStatus", () => {
  it("shows the phase, the shared budget and the current speaker", () => {
    renderStatus();
    expect(screen.getByRole("region", { name: "多人讨论" })).toBeInTheDocument();
    expect(screen.getByText("继续讨论")).toBeInTheDocument();
    expect(screen.getByText("已用 4 / 最多 7 个回合（含总结）")).toBeInTheDocument();
    expect(screen.getByText("当前发言：王也")).toBeInTheDocument();
    expect(screen.getByText("参与者：张楚岚、冯宝宝、王也")).toBeInTheDocument();
  });

  it("labels the opening and closing phases", () => {
    const { unmount } = renderStatus({ discussion: { phase: "opening" } });
    expect(screen.getByText("初次交流")).toBeInTheDocument();
    unmount();
    renderStatus({ discussion: { phase: "closing" } });
    expect(screen.getByText("正在收尾")).toBeInTheDocument();
  });

  it("requests a finish with the chosen summary participant", async () => {
    const user = userEvent.setup();
    const { props } = renderStatus();
    await user.selectOptions(screen.getByLabelText("收尾人（可选）"), "baobao");
    await user.click(screen.getByRole("button", { name: "本轮后收尾" }));
    expect(props.onRequestFinish).toHaveBeenCalledWith("baobao");
  });

  it("lets the host pick the summary participant when none is chosen", async () => {
    const user = userEvent.setup();
    const { props } = renderStatus();
    await user.click(screen.getByRole("button", { name: "本轮后收尾" }));
    expect(props.onRequestFinish).toHaveBeenCalledWith(undefined);
  });

  it("requests a stop", async () => {
    const user = userEvent.setup();
    const { props } = renderStatus();
    await user.click(screen.getByRole("button", { name: "本轮后停止" }));
    expect(props.onRequestStop).toHaveBeenCalledTimes(1);
  });

  it("only claims that the request was accepted while the turn finishes", () => {
    renderStatus({ discussion: { pending_control: "finish" } });
    const pending = screen.getByRole("status");
    expect(pending).toHaveTextContent("已请求收尾");
    expect(pending).toHaveTextContent("等当前回合结束后开始总结");
    expect(pending).toHaveTextContent("不会立即停止 CLI");
    expect(screen.getByRole("button", { name: "本轮后收尾" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "本轮后停止" })).toBeDisabled();
    expect(screen.getByLabelText("收尾人（可选）")).toBeDisabled();
  });

  it("shows the accepted stop request", () => {
    renderStatus({ discussion: { pending_control: "stop" } });
    expect(screen.getByRole("status")).toHaveTextContent("已请求停止");
  });

  it("disables the controls while a control request is in flight", () => {
    renderStatus({ busy: true });
    expect(screen.getByRole("button", { name: "本轮后收尾" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "本轮后停止" })).toBeDisabled();
  });

  it("reports unresolved name-calls that the summary will absorb", () => {
    renderStatus({ discussion: { unanswered_interrupt_ids: [11, 12] } });
    expect(screen.getByText("有 2 条点名没能单独回应，会在总结里处理。")).toBeInTheDocument();
  });

  it("reports a budget-exhausted ending and offers a continuation", async () => {
    const user = userEvent.setup();
    const { props } = renderStatus({
      discussion: {
        phase: "ended",
        turns_used: 7,
        turns_limit: 7,
        current_speaker: null,
        end_reason: "budget_exhausted",
        summary_message_ids: ["msg_summary"],
      },
      missionStatus: "completed",
    });
    expect(screen.getByText("已结束")).toBeInTheDocument();
    expect(
      screen.getByText("结束原因：回合预算用尽，正常结束 · 总结消息 1 条"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "继续讨论" }));
    expect(props.onContinue).toHaveBeenCalledTimes(1);
  });

  it("reports a provider failure and a user stop", () => {
    const { unmount } = renderStatus({
      discussion: { phase: "ended", end_reason: "provider_failed" },
      missionStatus: "blocked",
    });
    expect(screen.getByText("结束原因：Provider 失败")).toBeInTheDocument();
    expect(screen.getByText("任务状态：已阻塞")).toBeInTheDocument();
    unmount();
    renderStatus({
      discussion: { phase: "ended", end_reason: "user_stop" },
      missionStatus: "completed",
    });
    expect(screen.getByText("结束原因：你要求停止")).toBeInTheDocument();
  });

  it("falls back to the host order when a stale summary participant is selected", async () => {
    const user = userEvent.setup();
    const { props, rerender } = renderStatus({ discussion: { participant_ids: ["chulan", "baobao"] } });
    await user.selectOptions(screen.getByLabelText("收尾人（可选）"), "baobao");
    rerender(
      <DiscussionStatus
        {...props}
        discussion={makeDiscussion({ participant_ids: ["chulan", "wangye"] })}
      />,
    );
    await user.click(screen.getByRole("button", { name: "本轮后收尾" }));
    expect(props.onRequestFinish).toHaveBeenCalledWith(undefined);
  });
});
