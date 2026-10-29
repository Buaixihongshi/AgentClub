export interface MentionOption {
  id: string;
  label: string;
  aliases: string[];
  role: string;
  /** 拼音全拼，如 "zhangchulan"，用于 @ 检索。 */
  pinyin?: string;
  /** 拼音首字母缩写，如 "zcl"，用于 @ 检索。 */
  abbr?: string;
  /** 候选头像底色，按 agent 固定，用于 @ 浮层身份强调。 */
  color?: string;
  /** 用户在设置中新增的角色。 */
  custom?: boolean;
}

const DEFAULT_MENTION_OPTIONS: MentionOption[] = [
  { id: "chulan", label: "张楚岚", aliases: ["张楚岚", "chulan"], role: "通透机敏", pinyin: "zhangchulan", abbr: "zcl", color: "#2563eb" },
  { id: "baobao", label: "冯宝宝", aliases: ["冯宝宝", "宝宝", "baobao"], role: "坦率好奇", pinyin: "fengbaobao", abbr: "fbb", color: "#059669" },
  { id: "wangye", label: "王也", aliases: ["王也", "老王", "wangye"], role: "松弛笃定", pinyin: "wangye", abbr: "wy", color: "#d97706" },
];

// Mutable so loadMentionOptions() can sync with /api/agents at startup while
// keeping the export shape stable for existing tests and importers.
export const MENTION_OPTIONS: MentionOption[] = [...DEFAULT_MENTION_OPTIONS];

let aliasMap: Map<string, MentionOption> = buildAliasMap(MENTION_OPTIONS);

function buildAliasMap(options: MentionOption[]): Map<string, MentionOption> {
  const map = new Map<string, MentionOption>();
  for (const opt of options) {
    const keys = [opt.id, opt.label, ...opt.aliases];
    if (opt.pinyin) keys.push(opt.pinyin);
    if (opt.abbr) keys.push(opt.abbr);
    for (const key of keys) {
      map.set(key.toLowerCase(), opt);
    }
  }
  return map;
}

export function setMentionOptions(options: MentionOption[]): void {
  if (!options || options.length === 0) return;
  MENTION_OPTIONS.length = 0;
  // `/api/agents` 目前不下发 pinyin/abbr/color（见 App.tsx 的 setMentionOptions 调用），
  // 因此按 agent id 从内置默认值回填缺失字段，保证 @zcl 这类拼音缩写检索在运行时仍可用。
  const fallbackById = new Map(DEFAULT_MENTION_OPTIONS.map((opt) => [opt.id, opt]));
  MENTION_OPTIONS.push(
    ...options.map((opt) => {
      const fallback = fallbackById.get(opt.id);
      if (!fallback) return opt;
      return {
        ...opt,
        pinyin: opt.pinyin ?? fallback.pinyin,
        abbr: opt.abbr ?? fallback.abbr,
        color: opt.color ?? fallback.color,
      };
    }),
  );
  aliasMap = buildAliasMap(MENTION_OPTIONS);
}

/** Current directory entry for an agent id, including user-created roles. */
export function mentionOptionById(agentId: string): MentionOption | undefined {
  return MENTION_OPTIONS.find((option) => option.id === agentId);
}

/** Resolve a raw @-alias (name, latin alias, pinyin or abbr) to an option, case-insensitively. */
export function resolveMentionAlias(alias: string): MentionOption | undefined {
  return aliasMap.get(alias.trim().toLowerCase());
}

export function parseMentionTargets(raw: string): {
  content: string;
  targetAgentIds: string[];
} {
  let remainder = raw.trim();
  const ids: string[] = [];
  // Strip consecutive leading @-mentions. Stops at the first token that isn't
  // a recognized alias, so `@张楚岚 @冯宝宝 你俩...` yields [chulan, baobao] +
  // content="你俩..." while `@unknown rest` stays untouched.
  while (true) {
    const match = remainder.match(/^@(\S+?)(?:\s+|$)/);
    if (!match) break;
    const option = aliasMap.get(match[1].toLowerCase());
    if (!option) break;
    if (!ids.includes(option.id)) ids.push(option.id);
    remainder = remainder.slice(match[0].length).trimStart();
  }
  return { content: remainder.trim(), targetAgentIds: ids };
}

export function parseMentionTarget(raw: string): {
  content: string;
  targetAgentId?: string;
} {
  const { content, targetAgentIds } = parseMentionTargets(raw);
  return targetAgentIds.length > 0
    ? { content, targetAgentId: targetAgentIds[0] }
    : { content };
}

export function collectMentionTargetIds(raw: string): string[] {
  const ids: string[] = [];
  const mentionPattern = /@([^\s@，,。；;：:！!？?、)）\]}]+)/g;
  for (const match of raw.matchAll(mentionPattern)) {
    const option = aliasMap.get(match[1].toLowerCase());
    if (option && !ids.includes(option.id)) ids.push(option.id);
  }
  return ids;
}

export interface TypedMention {
  agentId: string;
  start: number;
  /** 排他边界，覆盖 `@别名`。 */
  end: number;
}

/**
 * 手动输入（未经浮层确认）的 `@别名`：按最长别名匹配；`@` 前紧跟拉丁字母数字时（邮箱）跳过，
 * 拉丁别名后不能紧跟字母数字（`@wangyes` 不算王也）。用户打全名后直接发送也应点名成功。
 */
export function findTypedMentions(raw: string): TypedMention[] {
  const keys = [...aliasMap.keys()].sort((a, b) => b.length - a.length);
  const found: TypedMention[] = [];
  const lower = raw.toLowerCase();
  for (let index = lower.indexOf("@"); index >= 0; index = lower.indexOf("@", index + 1)) {
    if (index > 0 && /[a-z0-9_.]/i.test(raw[index - 1])) continue; // 邮箱等 a@b 不算点名
    const key = keys.find((alias) => {
      if (!lower.startsWith(alias, index + 1)) return false;
      const next = raw[index + 1 + alias.length];
      return !(next && /[a-z0-9]$/i.test(alias) && /[a-z0-9]/i.test(next));
    });
    if (!key) continue;
    found.push({ agentId: aliasMap.get(key)!.id, start: index, end: index + 1 + key.length });
  }
  return found;
}

/** 去掉手动输入的 `@别名`（用于「纯 @提及 无正文」的空内容判定）。 */
export function stripTypedMentions(raw: string): string {
  let out = raw;
  for (const mention of findTypedMentions(raw).reverse()) {
    out = out.slice(0, mention.start) + out.slice(mention.end);
  }
  return out;
}

/**
 * Rank an option against a lowercased query. Lower is better; -1 means no match.
 * Order preference per the input-box spec: 完整姓名 > 姓名前缀 > 拼音全拼 > 首字母缩写 > 别名 > 拼音包含.
 */
function mentionMatchRank(opt: MentionOption, lower: string): number {
  const label = opt.label.toLowerCase();
  if (label === lower) return 0;
  if (label.startsWith(lower)) return 1;
  if (label.includes(lower)) return 2;
  const pinyin = opt.pinyin?.toLowerCase();
  if (pinyin && pinyin.startsWith(lower)) return 3;
  const abbr = opt.abbr?.toLowerCase();
  if (abbr && abbr.startsWith(lower)) return 4;
  if (opt.aliases.some((a) => a.toLowerCase().startsWith(lower))) return 5;
  if (pinyin && pinyin.includes(lower)) return 6;
  return -1;
}

export function getMentionSuggestions(query: string): MentionOption[] {
  if (!query) return MENTION_OPTIONS;
  const lower = query.toLowerCase();
  return MENTION_OPTIONS
    .map((opt) => ({ opt, rank: mentionMatchRank(opt, lower) }))
    .filter((entry) => entry.rank >= 0)
    .sort((a, b) => a.rank - b.rank)
    .map((entry) => entry.opt);
}

export function findActiveMention(value: string, cursorIndex: number): {
  query: string;
  start: number;
} | null {
  const before = value.slice(0, cursorIndex);
  // `[^\s@/]` keeps a trailing `/` out of the query so "@王也/" falls through
  // to skill-command detection instead of being captured as a mention query.
  const match = before.match(/@([^\s@/]*)$/);
  if (!match) return null;
  return {
    query: match[1],
    start: before.length - match[0].length,
  };
}

export function replaceActiveMention(
  value: string,
  cursorIndex: number,
  option: MentionOption,
): { newValue: string; newCursor: number; start: number } {
  const active = findActiveMention(value, cursorIndex);
  if (!active) return { newValue: value, newCursor: cursorIndex, start: cursorIndex };
  const replacement = `@${option.label} `;
  const before = value.slice(0, active.start);
  const after = value.slice(cursorIndex);
  const newValue = before + replacement + after;
  const newCursor = before.length + replacement.length;
  return { newValue, newCursor, start: active.start };
}
