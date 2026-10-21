import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DiscussionHighlights } from "../components/DiscussionHighlights";
import type { DiscussionResult, DiscussionView } from "../types";

const opinion: DiscussionResult = {
  id: "opinion-1", kind: "opinion", phase: "independent", agent_id: "chulan",
  data: { position_summary: "工具报错后，发出求助不代表恢复成功。", claims: [
    { id: "claim-1", statement: "需关联同一目标的成功返回。", evidence_status: "inference" },
  ], unknowns: ["替代工具是否执行成功？"], actions: ["补充失败到恢复的关联测试。"] },
};
const issue = {
  id: "issue-1", problem: "求助与恢复成功的判定存在分歧。", impact: "可能误导交付判断。", category: "approach", status: "open",
  positions: { wangye: { proposal: "单独显示处置进行中。" } },
};
const base: DiscussionView = { protocol: "deliberation_v2", participant_ids: ["chulan", "wangye"],
  phase: "independent", turns_used: 2, turns_limit: 9, results: [] };

describe("DiscussionHighlights", () => {
  it("shows the topic and actual member progress before any opinion is released", () => {
    render(<DiscussionHighlights goal="如何判断工具错误已经恢复？" discussion={{ ...base, progress: [
      { turn_id: "t1", agent_id: "chulan", phase: "independent", status: "submitted", attempt: 1 },
      { turn_id: "t2", agent_id: "wangye", phase: "independent", status: "running", attempt: 1 },
    ] }} />);
    expect(screen.getByRole("region", { name: "讨论重点" })).toBeVisible();
    expect(screen.getByText("如何判断工具错误已经恢复？")).toBeVisible();
    const progress = within(screen.getByRole("list", { name: "本轮成员进展" }));
    expect(progress.getByText("已提交")).toBeVisible();
    expect(progress.getByText("正在核对")).toBeVisible();
    expect(screen.getByText(/本批次全部提交后/)).toBeVisible();
    expect(screen.queryByText(opinion.data.position_summary!)).not.toBeInTheDocument();
  });

  it("updates from the running snapshot to real opinions without pretending they are final", async () => {
    const { rerender } = render(<DiscussionHighlights goal="议题" discussion={base} />);
    rerender(<DiscussionHighlights goal="议题" discussion={{ ...base, revision: 3, phase: "exchange", results: [opinion] }} />);
    expect(screen.getByText(opinion.data.position_summary!)).toBeVisible();
    expect(screen.queryByText(/本批次全部提交后/)).not.toBeInTheDocument();
    expect(screen.getByText(/最终结论以团队整理结果为准/)).toBeVisible();
    await userEvent.click(screen.getByText("主张与待验证事项"));
    expect(screen.getByText("推断")).toBeVisible();
    expect(screen.getByText("替代工具是否执行成功？")).toBeVisible();
    expect(screen.getByText("补充失败到恢复的关联测试。")).toBeVisible();
  });

  it("surfaces open issues ahead of resolved ones, keeps initial opinions expandable, and shows proposals", async () => {
    render(<DiscussionHighlights goal="议题" discussion={{ ...base, phase: "exchange", results: [opinion], issues: [
      { ...issue, id: "resolved", problem: "已处理的问题", status: "resolved" }, issue,
    ] }} />);
    expect(screen.getAllByText(/求助与恢复|已处理的问题/)[0]).toHaveTextContent(issue.problem);
    expect(screen.getByText(issue.problem)).toBeVisible();
    expect(screen.getByText(opinion.data.position_summary!)).not.toBeVisible();
    await userEvent.click(screen.getAllByText("查看影响与处理建议")[0]);
    expect(screen.getAllByText(/单独显示处置进行中/)[0]).toBeVisible();
    expect(screen.getAllByText(/可能误导交付判断/)[0]).toBeVisible();
    await userEvent.click(screen.getByText("成员观点 · 1 位"));
    expect(screen.getByText(opinion.data.position_summary!)).toBeVisible();
  });

  it("uses only the latest exchange turn instead of mixing previous rounds into current progress", () => {
    render(<DiscussionHighlights goal="议题" discussion={{ ...base, phase: "exchange", exchange_agents: ["chulan"], results: [opinion], progress: [
      { turn_id: "old", agent_id: "chulan", phase: "exchange", status: "submitted", attempt: 1 },
      { turn_id: "new", agent_id: "chulan", phase: "exchange", status: "running", attempt: 1 },
    ] }} />);
    const progress = within(screen.getByRole("list", { name: "本轮成员进展" }));
    expect(progress.getAllByRole("listitem")).toHaveLength(1);
    expect(progress.getByText("正在核对")).toBeVisible();
    expect(progress.queryByText("已提交")).not.toBeInTheDocument();
  });

  it("labels an unreviewed draft and shows review findings as substantive progress", () => {
    render(<DiscussionHighlights goal="议题" discussion={{ ...base, phase: "finalizing", draft_id: "draft", results: [
      { id: "draft", agent_id: "chulan", phase: "synthesis", kind: "draft", data: { sections: [
        { id: "recommendation", content: "区分已披露、处置中和恢复成功。", source_ids: [] },
      ] } },
      { id: "review", agent_id: "wangye", phase: "review", kind: "review", data: { findings: [
        { id: "f1", section_id: "recommendation", problem: "尚缺失败重试的验证。", desired_change: "补充测试后再决定。", blocking: true },
      ] } },
    ] }} />);
    expect(screen.getByText(/综合建议 · 待复核/)).toBeVisible();
    expect(screen.getByText("区分已披露、处置中和恢复成功。")).toBeVisible();
    expect(screen.getByText("需优先处理：")).toBeVisible();
    expect(screen.getByText(/尚缺失败重试的验证/)).toBeVisible();
  });

  it("keeps the completed process available without duplicating the final response", async () => {
    render(<DiscussionHighlights goal="议题" discussion={{ ...base, phase: "ended", final_id: "final", results: [opinion,
      { id: "final", agent_id: "chulan", phase: "finalizing", kind: "draft", data: { sections: [{ id: "recommendation", content: "最终建议正文", source_ids: [] }] } },
    ] }} />);
    expect(screen.getByText("查看本轮讨论过程")).toBeVisible();
    expect(screen.queryByText("最终建议正文")).not.toBeInTheDocument();
    expect(screen.getByText(opinion.data.position_summary!)).not.toBeVisible();
    await userEvent.click(screen.getByText("查看本轮讨论过程"));
    await userEvent.click(screen.getByText("成员观点 · 1 位"));
    expect(screen.getByText(opinion.data.position_summary!)).toBeVisible();
  });

  it("does not show a fabricated opinion for an interrupted run with no released result", () => {
    render(<DiscussionHighlights goal="议题" discussion={{ ...base, phase: "ended", end_reason: "user_stop" }} />);
    expect(screen.getByText("本轮尚未形成可展示的讨论观点。")).toBeInTheDocument();
    expect(screen.queryByText(/本批次全部提交后/)).not.toBeInTheDocument();
  });
});
