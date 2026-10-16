import { getMentionSuggestions } from "./mentions";
import type {
  DiscussionControlAction,
  DiscussionEndReason,
  DiscussionPhase,
  DiscussionView,
} from "../types";

export interface DiscussionParticipantOption {
  agentId: string;
  label: string;
  role: string;
}

/**
 * Config-ordered agent directory. `mentions.ts` keeps its options synced with
 * `/api/agents` at startup, so roundtable pickers show the same names and
 * personality tags the backend prompts use.
 */
export function discussionParticipantOptions(): DiscussionParticipantOption[] {
  return getMentionSuggestions("").map((option) => ({
    agentId: option.id,
    label: option.label,
    role: option.role,
  }));
}

export function allParticipantIds(): string[] {
  return discussionParticipantOptions().map((option) => option.agentId);
}

export function participantLabel(
  agentId?: string | null,
  options?: DiscussionParticipantOption[],
): string {
  if (!agentId) return "未指定";
  const pool = options ?? discussionParticipantOptions();
  return pool.find((option) => option.agentId === agentId)?.label ?? agentId;
}

/** Default budget the backend applies when the client omits max_turns. */
export function defaultDiscussionTurnLimit(participantCount: number): number {
  return participantCount * 2 + 1;
}

const PHASE_LABEL: Record<DiscussionPhase, string> = {
  opening: "初次交流",
  exchange: "继续讨论",
  closing: "正在收尾",
  ended: "已结束",
};

export function formatDiscussionPhase(phase?: string | null): string {
  if (!phase) return "未开始";
  return PHASE_LABEL[phase as DiscussionPhase] ?? phase;
}

const END_REASON_LABEL: Record<DiscussionEndReason, string> = {
  budget_exhausted: "回合预算用尽，正常结束",
  user_finish: "你要求收尾",
  user_stop: "你要求停止",
  provider_failed: "Provider 失败",
  summary_failed: "总结失败",
  service_restart: "后端重启中断",
  runner_failed: "运行器异常",
  no_successful_turn: "没有成功的发言",
};

export function formatDiscussionEndReason(reason?: string | null): string {
  if (!reason) return "";
  return END_REASON_LABEL[reason as DiscussionEndReason] ?? reason;
}

export function formatDiscussionControl(action?: DiscussionControlAction | null): string {
  if (action === "finish") {
    return "已请求收尾：等当前回合结束后开始总结，期间不会立即停止 CLI。";
  }
  if (action === "stop") {
    return "已请求停止：等当前回合结束后结束讨论，期间不会立即停止 CLI。";
  }
  return "";
}

export function formatDiscussionBudget(turnsUsed: number, turnsLimit: number): string {
  return `已用 ${turnsUsed} / 最多 ${turnsLimit} 个回合（含总结）`;
}

export function discussionIsActive(discussion?: DiscussionView | null): boolean {
  return Boolean(discussion && discussion.phase !== "ended");
}

/** Turns the backend's English conflicts into actionable Chinese hints. */
export function formatDiscussionError(raw?: string | null): string {
  const message = (raw ?? "").trim();
  if (!message) return "请求失败";
  const rules: Array<[RegExp, string]> = [
    [/closing or stopping/i, "讨论已进入收尾或停止，这条插话没有发送。输入仍保留，可点“继续讨论”后再用。"],
    [/discussion has ended/i, "这轮讨论已经结束，插话未发送。点“继续讨论”可以带着原话题开新一轮。"],
    [/already has an active mission/i, "这个会话已有正在运行的讨论，等它结束后再开始新的。"],
    [/archived/i, "会话已归档，恢复后才能继续讨论。"],
    [/target must be a discussion participant/i, "只能点名本次讨论的参与者。"],
    [/unknown discussion participant/i, "参与者不在配置里，请重新选择角色。"],
    [/participant/i, "参与者不符合要求：请选择 2～3 位角色。"],
    [/roundtable requires discussion configuration/i, "多人讨论需要先选择参与者。"],
    [/exceeds server roundtable_max_turns/i, "回合数超过服务端上限，请减少参与者或降低预算。"],
    [/request_id/i, "请求标识冲突，请刷新后重试。"],
    [/conversation not found/i, "会话不存在，请刷新会话列表。"],
    [/failed to fetch|networkerror|load failed/i, "无法连接后端，请确认服务在运行后重试。"],
  ];
  for (const [pattern, friendly] of rules) {
    if (pattern.test(message)) return friendly;
  }
  return message;
}
