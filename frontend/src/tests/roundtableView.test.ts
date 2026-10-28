import { describe, expect, it } from "vitest";
import {
  defaultDiscussionTurnLimit,
  discussionIsActive,
  discussionParticipantOptions,
  formatDiscussionBudget,
  formatDiscussionControl,
  formatDiscussionEndReason,
  formatDiscussionError,
  formatDiscussionPhase,
  participantLabel,
} from "../lib/roundtableView";

describe("roundtable view helpers", () => {
  it("lists configured participants with personality tags, in config order", () => {
    const options = discussionParticipantOptions();
    expect(options.map((option) => option.agentId)).toEqual(["chulan", "baobao", "wangye"]);
    expect(options.map((option) => option.role)).toEqual(["通透机敏", "坦率好奇", "松弛笃定"]);
    expect(participantLabel("wangye")).toBe("王也");
    expect(participantLabel(null)).toBe("未指定");
    expect(participantLabel("nobody")).toBe("nobody");
  });

  it("labels every discussion phase", () => {
    expect(formatDiscussionPhase("opening")).toBe("初次交流");
    expect(formatDiscussionPhase("exchange")).toBe("继续讨论");
    expect(formatDiscussionPhase("closing")).toBe("正在收尾");
    expect(formatDiscussionPhase("ended")).toBe("已结束");
    expect(formatDiscussionPhase(null)).toBe("未开始");
  });

  it("labels every end reason the backend can report", () => {
    expect(formatDiscussionEndReason("responses_complete")).toBe("本次回应已完成");
    expect(formatDiscussionEndReason("budget_exhausted")).toBe("回合预算用尽，正常结束");
    expect(formatDiscussionEndReason("user_finish")).toBe("你要求收尾");
    expect(formatDiscussionEndReason("user_stop")).toBe("你要求停止");
    expect(formatDiscussionEndReason("provider_failed")).toBe("Provider 失败");
    expect(formatDiscussionEndReason("summary_failed")).toBe("总结失败");
    expect(formatDiscussionEndReason("service_restart")).toBe("后端重启中断");
    expect(formatDiscussionEndReason("runner_failed")).toBe("运行器异常");
    expect(formatDiscussionEndReason("no_successful_turn")).toBe("没有成功的发言");
    expect(formatDiscussionEndReason(null)).toBe("");
  });

  it("describes budget and pending control without promising an immediate stop", () => {
    expect(formatDiscussionBudget(4, 7)).toBe("已用 4 / 最多 7 个回合（含总结）");
    expect(defaultDiscussionTurnLimit(3)).toBe(7);
    expect(defaultDiscussionTurnLimit(2)).toBe(5);

    const finish = formatDiscussionControl("finish");
    expect(finish).toContain("等当前回合结束后开始总结");
    expect(finish).toContain("不会立即停止 CLI");

    const stop = formatDiscussionControl("stop");
    expect(stop).toContain("等当前回合结束后结束讨论");
    expect(stop).toContain("不会立即停止 CLI");
    expect(formatDiscussionControl(null)).toBe("");
  });

  it("keeps the discussion active until the server reports ended", () => {
    expect(
      discussionIsActive({
        participant_ids: ["chulan", "baobao"],
        phase: "closing",
        turns_used: 3,
        turns_limit: 5,
      }),
    ).toBe(true);
    expect(
      discussionIsActive({
        participant_ids: ["chulan", "baobao"],
        phase: "ended",
        turns_used: 5,
        turns_limit: 5,
      }),
    ).toBe(false);
    expect(discussionIsActive(null)).toBe(false);
    expect(discussionIsActive(undefined)).toBe(false);
  });

  it("turns backend conflicts into actionable Chinese hints", () => {
    expect(
      formatDiscussionError("Discussion is closing or stopping; save this input for the next discussion"),
    ).toContain("输入仍保留");
    expect(formatDiscussionError("Discussion has ended; continue in a new mission")).toContain(
      "继续讨论",
    );
    expect(formatDiscussionError("Conversation already has an active mission")).toContain(
      "已有正在运行的讨论",
    );
    expect(formatDiscussionError("Conversation is archived")).toContain("恢复");
    expect(formatDiscussionError("Target must be a discussion participant")).toContain(
      "只能点名本次讨论的参与者",
    );
    expect(formatDiscussionError("Unknown discussion participant")).toContain("配置");
    expect(formatDiscussionError("提交内容不合规")).toBe("提交内容不合规");
    expect(formatDiscussionError("")).toBe("请求失败");
    expect(formatDiscussionError(undefined)).toBe("请求失败");
  });
});
