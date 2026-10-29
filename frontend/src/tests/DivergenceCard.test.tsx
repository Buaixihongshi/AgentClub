import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DivergenceCard } from "../components/DivergenceCard";
import type { DiscussionView, MissionStatus } from "../types";

const CONFLICT = { decision_key: "是否默认开启", positions: [
  { agent_id: "chulan", claim_id: "c1", recommendation: "默认开启", conditions: [], evidence_status: "unverified" },
  { agent_id: "baobao", claim_id: "c2", recommendation: "暂缓开启", conditions: [], evidence_status: "verified" },
] };
const AGREED = { decision_key: "写入前确认", positions: [
  { agent_id: "chulan", claim_id: "c3", recommendation: "需要确认", conditions: [], evidence_status: "unverified" },
  { agent_id: "baobao", claim_id: "c4", recommendation: "需要确认", conditions: [], evidence_status: "unverified" },
] };

function view(patch: Partial<DiscussionView> = {}): DiscussionView {
  return { protocol: "deliberation_v2", depth: "quick", participant_ids: ["chulan", "baobao"], phase: "choosing",
    awaiting_reason: "choose_next", turns_used: 2, turns_limit: 9, revision: 5, results: [],
    choices: ["verify_conflicts", "synthesize", "user_decide", "escalate", "finish"],
    divergence: { agreed: [AGREED], conflicts: [CONFLICT], single: [] }, ...patch };
}

function setup(patch: Partial<DiscussionView> = {}, status: MissionStatus = "awaiting_input") {
  const onControl = vi.fn();
  render(<DivergenceCard discussion={view(patch)} missionStatus={status} onControl={onControl} />);
  return onControl;
}

describe("DivergenceCard", () => {
  it("shows per-member progress without content while voices think independently", () => {
    setup({ phase: "independent", awaiting_reason: null, choices: [], divergence: null, progress: [
      { turn_id: "t1", agent_id: "chulan", phase: "independent", status: "submitted", attempt: 1 },
      { turn_id: "t2", agent_id: "baobao", phase: "independent", status: "running", attempt: 1 },
    ] }, "running");
    const region = screen.getByRole("region", { name: "讨论进展" });
    expect(region).toHaveTextContent("全部完成后一起公开");
    expect(region).toHaveTextContent("张楚岚 已完成");
    expect(region).toHaveTextContent("冯宝宝 正在思考");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("lays out agreement and the split, honest about the comparison and its cost", () => {
    setup({ pending_inputs: ["msg_1"] });
    const card = screen.getByRole("region", { name: "分歧卡" });
    expect(card).toHaveTextContent("分歧是否默认开启：张楚岚 默认开启 / 冯宝宝 暂缓开启");
    expect(card).toHaveTextContent("一致写入前确认：需要确认");
    expect(card).toHaveTextContent("措辞不同的分歧可能漏掉");
    expect(card).toHaveTextContent("已用 2 次调用");
    expect(card).toHaveTextContent("已收到 1 条补充，会带入下一步");
  });

  it("says plainly when the voices' decision keys do not line up", () => {
    setup({ choices: ["synthesize", "user_decide", "escalate", "finish"], divergence: { agreed: [], conflicts: [], single: [
      { decision_key: "context_memory_default", positions: [CONFLICT.positions[0]] },
      { decision_key: "memory_default_enabled", positions: [CONFLICT.positions[1]] }] } });
    const card = screen.getByRole("region", { name: "分歧卡" });
    expect(card).toHaveTextContent("无法自动比对");
    expect(card).not.toHaveTextContent("没有发现明确分歧");
    expect(screen.getByText("context_memory_default：", { exact: false })).toBeVisible();
  });

  it("offers only the server's choices and sends each with its fields", async () => {
    const user = userEvent.setup();
    const onControl = setup({ choices: ["synthesize", "user_decide", "escalate", "finish"] });
    expect(screen.queryByRole("button", { name: "查证这个分歧" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "让其综合" }));
    expect(onControl).toHaveBeenLastCalledWith("synthesize", { agent_id: "chulan" });
    await user.selectOptions(screen.getByRole("combobox", { name: "综合者" }), "baobao");
    await user.click(screen.getByRole("button", { name: "让其综合" }));
    expect(onControl).toHaveBeenLastCalledWith("synthesize", { agent_id: "baobao" });
    await user.click(screen.getByRole("button", { name: "正式评审" }));
    expect(onControl).toHaveBeenLastCalledWith("escalate", undefined);
    await user.click(screen.getByRole("button", { name: "就到这里" }));
    expect(onControl).toHaveBeenLastCalledWith("finish", undefined);
  });

  it("requires a written decision before recording it", async () => {
    const user = userEvent.setup();
    const onControl = setup();
    await user.click(screen.getByRole("button", { name: "我来定" }));
    await user.click(screen.getByRole("button", { name: "记录决定" }));
    expect(screen.getByRole("alert")).toHaveTextContent("先写下你的决定");
    expect(onControl).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("你的决定"), "先不默认开启");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "记录决定" }));
    expect(onControl).toHaveBeenLastCalledWith("user_decide", { text: "先不默认开启" });
  });

  it("stays out of full reviews and finished discussions", () => {
    const { container: full } = render(<DivergenceCard discussion={view({ depth: "full" })} missionStatus="awaiting_input" />);
    expect(full).toBeEmptyDOMElement();
    const { container: ended } = render(<DivergenceCard discussion={view({ phase: "ended" })} missionStatus="completed" />);
    expect(ended).toBeEmptyDOMElement();
  });
});
