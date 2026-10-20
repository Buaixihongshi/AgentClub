import type { MentionOption } from "./mentions";

/**
 * 已确认提及的「实体范围」。只有经浮层 Tab/Enter/点击确认过的 @提及才进入这里，
 * 直接输入或粘贴的 `@别名 ` 不会产生范围，因此发送链路不再从全文反解析路由。
 */
export interface ConfirmedMention {
  agentId: string;
  /** 确认时插入的精确文本，如 `@张楚岚 `（含尾随空格），用于编辑后完整性校验。 */
  text: string;
  start: number;
  /** 排他边界，覆盖 `text` 的完整区间（含尾随空格）。 */
  end: number;
}

/**
 * 依据一次纯文本编辑（前缀/后缀 diff）平移或失效已确认提及。
 * - 完全位于编辑区之前的提及保持不变；
 * - 完全位于编辑区之后的提及按长度差平移（在提及前插入文字不会破坏绑定）；
 * - 与编辑区重叠的提及直接失效（编辑片段即解绑）。
 * 最后再按当前文本做一次完整性校验，兜底捕获尾随空格被删除等边界。
 */
export function applyEdit(
  prev: string,
  next: string,
  mentions: ConfirmedMention[],
): ConfirmedMention[] {
  let prefix = 0;
  const maxPrefix = Math.min(prev.length, next.length);
  while (prefix < maxPrefix && prev[prefix] === next[prefix]) prefix++;

  let suffix = 0;
  const maxSuffix = Math.min(prev.length - prefix, next.length - prefix);
  while (
    suffix < maxSuffix &&
    prev[prev.length - 1 - suffix] === next[next.length - 1 - suffix]
  ) {
    suffix++;
  }

  const removedStart = prev.length - suffix;
  const delta = next.length - prev.length;

  const shifted: ConfirmedMention[] = [];
  for (const m of mentions) {
    if (m.end <= prefix) {
      shifted.push(m);
    } else if (m.start >= removedStart) {
      shifted.push({ ...m, start: m.start + delta, end: m.end + delta });
    }
    // 与编辑区重叠 → 失效，不保留。
  }
  return shifted.filter((m) => next.slice(m.start, m.end) === m.text);
}

/**
 * 记录一次浮层确认产生的提及。`start` 为被替换的 `@query` 起始位置
 * （由 replaceActiveMention 返回），插入文本为 `@label `。
 */
export function confirmMention(
  prev: string,
  next: string,
  start: number,
  option: MentionOption,
  mentions: ConfirmedMention[],
): ConfirmedMention[] {
  const text = `@${option.label} `;
  const shifted = applyEdit(prev, next, mentions);
  return [
    ...shifted,
    { agentId: option.id, text, start, end: start + text.length },
  ];
}

/** 发送链路消费的确认元数据：按确认顺序输出去重后的目标 agent id。 */
export function confirmedMentionIds(mentions: ConfirmedMention[]): string[] {
  const ids: string[] = [];
  for (const m of mentions) {
    if (!ids.includes(m.agentId)) ids.push(m.agentId);
  }
  return ids;
}

/** 去掉已确认提及的精确文本（用于「纯 @提及 无正文」的空内容判定）。 */
export function stripConfirmedMentions(
  value: string,
  mentions: ConfirmedMention[],
): string {
  const valid = mentions
    .filter((m) => value.slice(m.start, m.end) === m.text)
    .sort((a, b) => b.start - a.start);
  let out = value;
  for (const m of valid) out = out.slice(0, m.start) + out.slice(m.end);
  return out;
}
