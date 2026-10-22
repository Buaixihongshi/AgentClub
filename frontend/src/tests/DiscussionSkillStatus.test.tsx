import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DiscussionSkillStatus } from "../components/DiscussionSkillStatus";
import type { DiscussionView, MissionStatus } from "../types";

function view(patch: Partial<DiscussionView> = {}): DiscussionView {
  return { protocol: "deliberation_v2", participant_ids: ["chulan", "baobao", "wangye"], phase: "independent", turns_used: 3, turns_limit: 13, results: [], ...patch };
}

function setup(patch: Partial<DiscussionView> = {}, missionStatus: MissionStatus = "running", busy = false) {
  const props = { discussion: view(patch), missionStatus, busy, onControl: vi.fn(), onRequestFinish: vi.fn(), onRequestStop: vi.fn(), onContinue: vi.fn() };
  return { ...render(<DiscussionSkillStatus {...props} />), props };
}

describe("DiscussionSkillStatus", () => {
  it("keeps the run compact without stages, role cards or implementation metadata", async () => {
    const { props } = setup({ rules_version: "internal-version", build_id: "private-build", progress: [{ turn_id: "t1", agent_id: "chulan", phase: "independent", status: "submitted", attempt: 1 }] });
    expect(screen.getByRole("status")).toHaveTextContent("团队正在形成独立判断…");
    expect(screen.queryByRole("list", { name: "讨论流程" })).not.toBeInTheDocument();
    expect(screen.queryByText(/张楚岚/)).not.toBeInTheDocument();
    expect(screen.queryByText(/internal-version|private-build|13/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "停止讨论" }));
    expect(props.onControl).toHaveBeenCalledWith("stop", undefined);
  });

  it.each([
    ["exchange", "核对分歧与依据"], ["synthesis", "整理各方观点"],
    ["review", "复核综合建议"], ["finalizing", "按复核意见修订"],
  ] as const)("describes the actual %s phase", (phase, label) => {
    setup({ phase });
    expect(screen.getByRole("status")).toHaveTextContent(label);
  });

  it("explains when phase 2 was skipped without claiming verification", () => {
    setup({ phase: "awaiting_synthesizer", agreement: { skip: true, reason: "decisions_agree", shared_decision_keys: ["release"] } }, "awaiting_input");
    expect(screen.getByText(/已跳过分歧讨论/)).toBeInTheDocument();
    expect(screen.getByText(/不代表事实已验证/)).toBeInTheDocument();
  });

  it("waits for an explicit synthesizer selection and never defaults to a person", async () => {
    const user = userEvent.setup();
    const { props } = setup({ phase: "awaiting_synthesizer", awaiting_reason: "select_synthesizer" }, "awaiting_input");
    expect(screen.getByLabelText("负责综合的伙伴")).toHaveValue("");
    expect(screen.getByRole("button", { name: "由其综合" })).toBeDisabled();
    expect(props.onControl).not.toHaveBeenCalled();
    await user.selectOptions(screen.getByLabelText("负责综合的伙伴"), "wangye");
    await user.click(screen.getByRole("button", { name: "由其综合" }));
    expect(props.onControl).toHaveBeenCalledWith("select_synthesizer", { agent_id: "wangye" });
  });

  it("leaves value choices to the user and supports explicit deferral", async () => {
    const { props } = setup({ awaiting_reason: "resolve_value_choice", value_decisions: [{ id: "risk", question: "选择风险偏好", options: [{ id: "safe", label: "稳健" }] }] }, "awaiting_input");
    expect(props.onControl).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "明确留待以后决定" }));
    expect(props.onControl).toHaveBeenCalledWith("record_decision", { decision_id: "risk", option_id: "defer" });
  });

  it("only restarts with complete user-supplied conditions", async () => {
    const user = userEvent.setup();
    const { props } = setup({ awaiting_reason: "revise_input", pending_inputs: ["input1"] }, "awaiting_input");
    expect(screen.getByRole("button", { name: "以新条件重新讨论" })).toBeDisabled();
    await user.type(screen.getByLabelText("完整的新议题"), "预算只有一周，重新安排范围");
    await user.click(screen.getByRole("button", { name: "以新条件重新讨论" }));
    expect(props.onControl).toHaveBeenCalledWith("restart_with_input", { input_ids: ["input1"], goal: "预算只有一周，重新安排范围" });
    await user.click(screen.getByRole("button", { name: "补充留待下次，继续原议题" }));
    expect(props.onControl).toHaveBeenCalledWith("defer_input", { input_ids: ["input1"] });
  });

  it("preserves every provider failure and allows retrying the failed batch", async () => {
    const user = userEvent.setup();
    const { props } = setup({ retryable: true, progress: [
      { turn_id: "t1", agent_id: "chulan", provider_id: "codex", phase: "independent", status: "failed", attempt: 1, exit_code: 1, error: "认证失败" },
      { turn_id: "t2", agent_id: "baobao", provider_id: "claude", phase: "independent", status: "interrupted", attempt: 1, exit_code: 2, error: "临时目录不可用" },
    ] }, "blocked");
    expect(screen.getByRole("alert")).toHaveTextContent("已提交的判断会保留");
    await user.click(screen.getByText("查看运行错误"));
    expect(screen.getByText("张楚岚 / codex")).toBeVisible();
    expect(screen.getByText("冯宝宝 / claude")).toBeVisible();
    expect(screen.getByText(/退出码 2/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "重试失败批次" }));
    expect(props.onControl).toHaveBeenCalledWith("retry", { turn_id: "t1" });
  });

  it("does not offer an exhausted retry or pretend the run completed", () => {
    setup({ retryable: false }, "blocked");
    expect(screen.queryByRole("button", { name: "重试失败批次" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("重试额度已用尽");
    expect(screen.getByRole("button", { name: "停止讨论" })).toBeEnabled();
    expect(screen.getByRole("status")).not.toHaveTextContent("已完成");
  });

  it("shows completion and optional knowledge saving without repeating the final chat response", async () => {
    const { props } = setup({ phase: "ended", final_id: "draft", results: [{ id: "draft", kind: "draft", agent_id: "wangye", phase: "synthesis", data: { sections: [{ id: "recommendation", content: "由聊天展示的最终结论", source_ids: [] }] } }] }, "completed");
    expect(screen.getByRole("status")).toHaveTextContent("团队讨论已完成");
    expect(screen.queryByText("由聊天展示的最终结论")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "停止讨论" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "保存为知识候选" }));
    expect(props.onControl).toHaveBeenCalledWith("save_knowledge_candidate", undefined);
  });

  it("disables all pending controls and does not treat a user stop as completion", () => {
    const { unmount } = setup({ pending_control: "stop" });
    expect(screen.getByRole("status")).toHaveTextContent("正在停止团队讨论");
    expect(screen.getByRole("button", { name: "停止讨论" })).toBeDisabled();
    unmount();
    setup({ phase: "ended", end_reason: "user_stop" }, "completed");
    expect(screen.getByRole("status")).toHaveTextContent("讨论已停止");
    expect(screen.queryByRole("button", { name: "保存为知识候选" })).not.toBeInTheDocument();
  });

  it("keeps lightweight legacy run controls usable", async () => {
    const user = userEvent.setup();
    const { props, unmount } = setup({ protocol: "roundtable_v1", phase: "exchange" });
    await user.click(screen.getByRole("button", { name: "整理结果" }));
    await user.click(screen.getByRole("button", { name: "停止讨论" }));
    expect(props.onRequestFinish).toHaveBeenCalledOnce();
    expect(props.onRequestStop).toHaveBeenCalledOnce();
    unmount();
    const legacy = setup({ protocol: "roundtable_v1", phase: "ended" }, "completed");
    await user.click(screen.getByRole("button", { name: "继续讨论" }));
    expect(legacy.props.onContinue).toHaveBeenCalledOnce();
  });
});
