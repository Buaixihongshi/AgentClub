import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DeliberationStatus } from "../components/DeliberationStatus";
import type { DiscussionView } from "../types";

function view(patch: Partial<DiscussionView> = {}): DiscussionView {
  return { protocol: "deliberation_v2", participant_ids: ["chulan", "baobao", "wangye"],
    phase: "independent", turns_used: 3, turns_limit: 13, revision: 7,
    progress: [{ agent_id: "chulan", turn_id: "t1", phase: "independent", status: "submitted", attempt: 1 }],
    results: [], ...patch };
}

describe("six-phase discussion controls", () => {
  it("shows sealed progress without presenting an early opinion", () => {
    render(<DeliberationStatus discussion={view()} missionStatus="running" onContinue={vi.fn()} onControl={vi.fn()} />);
    expect(screen.getByText(/全部提交后统一展示/)).toBeInTheDocument();
    expect(screen.getByText(/张楚岚 · 已提交/)).toBeInTheDocument();
    expect(screen.queryByText(/初始判断/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "由其综合" })).not.toBeInTheDocument();
  });

  it("requires an explicit synthesizer selection with no preselected person", async () => {
    const control = vi.fn();
    const user = userEvent.setup();
    render(<DeliberationStatus discussion={view({ phase: "awaiting_synthesizer", awaiting_reason: "select_synthesizer" })}
      missionStatus="awaiting_input" onContinue={vi.fn()} onControl={control} />);
    expect(screen.getByRole("button", { name: "由其综合" })).toBeDisabled();
    expect(control).not.toHaveBeenCalled();
    await user.selectOptions(screen.getByRole("combobox"), "wangye");
    await user.click(screen.getByRole("button", { name: "由其综合" }));
    expect(control).toHaveBeenCalledWith("select_synthesizer", { agent_id: "wangye" });
  });

  it("keeps value decisions waiting and records explicit deferral", async () => {
    const control = vi.fn();
    render(<DeliberationStatus discussion={view({ phase: "finalizing", awaiting_reason: "resolve_value_choice",
      value_decisions: [{ id: "risk", question: "选择风险偏好", options: [{ id: "safe", label: "稳健" }, { id: "fast", label: "快速" }] }], decisions: {} })}
      missionStatus="awaiting_input" onContinue={vi.fn()} onControl={control} />);
    expect(control).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "明确留待以后决定" }));
    expect(control).toHaveBeenCalledWith("record_decision", { decision_id: "risk", option_id: "defer" });
  });

  it("shows a retryable failed batch without pretending discussion completed", async () => {
    const control = vi.fn();
    render(<DeliberationStatus discussion={view({ retryable: true,
      progress: [{ agent_id: "baobao", turn_id: "failed", phase: "independent", status: "failed", attempt: 1, error: "timeout" }] })}
      missionStatus="blocked" onContinue={vi.fn()} onControl={control} />);
    expect(screen.getByText(/未提前解封/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "继续讨论" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试失败批次" }));
    expect(control).toHaveBeenCalledWith("retry", { turn_id: "failed" });
  });

  it("only sends revised conditions through an explicit new-discussion action", async () => {
    const control = vi.fn();
    render(<DeliberationStatus discussion={view({ awaiting_reason: "revise_input", pending_inputs: ["new-input"] })}
      missionStatus="awaiting_input" onContinue={vi.fn()} onControl={control} />);
    expect(screen.getByRole("button", { name: "以新条件重新讨论" })).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "预算只有一周");
    await userEvent.click(screen.getByRole("button", { name: "以新条件重新讨论" }));
    expect(control).toHaveBeenCalledWith("restart_with_input", { input_ids: ["new-input"], goal: "预算只有一周" });
  });

  it("explains a skipped exchange and shows technical issue dispositions", async () => {
    render(<DeliberationStatus discussion={view({
      phase: "finalizing", draft_id: "dr1", final_id: "dr1", synthesizer_id: "chulan",
      agreement: { skip: true, reason: "decisions_agree", shared_decision_keys: ["release"] },
      issues: [{ id: "issue_1", problem: "发布策略不兼容", impact: "影响上线风险",
        category: "approach", status: "resolved", technical_decision: "采用灰度方案", decision_status: "resolved" }],
      results: [{ id: "dr1", kind: "draft", agent_id: "chulan", phase: "finalizing",
        data: { sections: [], issue_decisions: [{ issue_id: "issue_1", status: "resolved", decision: "采用灰度方案" }] } }],
    })} missionStatus="running" onContinue={vi.fn()} onControl={vi.fn()} />);
    expect(screen.getByText(/已跳过分歧讨论/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("技术问题处置（1）"));
    expect(screen.getAllByText(/采用灰度方案/).length).toBeGreaterThan(0);
  });

  it("shows every failed provider and distinguishes attempts from stage calls", async () => {
    render(<DeliberationStatus discussion={view({ attempts_used: 6, retryable: false, progress: [
      { agent_id: 'chulan', provider_id: 'codex', turn_id: 't1', phase: 'independent', status: 'failed', attempt: 2, exit_code: 1, error: '管理偏好初始化失败' },
      { agent_id: 'baobao', provider_id: 'claude', turn_id: 't2', phase: 'independent', status: 'failed', attempt: 2, exit_code: 1, error: '临时目录不可用' },
      { agent_id: 'wangye', provider_id: 'antigravity', turn_id: 't3', phase: 'independent', status: 'failed', attempt: 2, exit_code: 2, error: '启动参数错误' },
    ] })} missionStatus="blocked" onContinue={vi.fn()} onControl={vi.fn()} />);
    await userEvent.click(screen.getByText('查看运行错误'));
    expect(screen.getByText('张楚岚 / codex')).toBeVisible();
    expect(screen.getByText('冯宝宝 / claude')).toBeVisible();
    expect(screen.getByText('王也 / antigravity')).toBeVisible();
    expect(screen.getByText(/退出码 2/)).toBeVisible();
    await userEvent.click(screen.getByText('运行详情'));
    expect(screen.getByText(/实际运行尝试 6 次/)).toBeVisible();
  });
});
