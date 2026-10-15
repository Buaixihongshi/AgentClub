export interface MentionOption {
  id: string;
  label: string;
  aliases: string[];
  role: string;
}

const DEFAULT_MENTION_OPTIONS: MentionOption[] = [
  { id: "chulan", label: "张楚岚", aliases: ["张楚岚", "chulan"], role: "通透机敏" },
  { id: "baobao", label: "冯宝宝", aliases: ["冯宝宝", "宝宝", "baobao"], role: "坦率好奇" },
  { id: "wangye", label: "王也", aliases: ["王也", "老王", "wangye"], role: "松弛笃定" },
];

// Mutable so loadMentionOptions() can sync with /api/agents at startup while
// keeping the export shape stable for existing tests and importers.
export const MENTION_OPTIONS: MentionOption[] = [...DEFAULT_MENTION_OPTIONS];

let aliasMap: Map<string, MentionOption> = buildAliasMap(MENTION_OPTIONS);

function buildAliasMap(options: MentionOption[]): Map<string, MentionOption> {
  const map = new Map<string, MentionOption>();
  for (const opt of options) {
    map.set(opt.id.toLowerCase(), opt);
    map.set(opt.label.toLowerCase(), opt);
    for (const alias of opt.aliases) {
      map.set(alias.toLowerCase(), opt);
    }
  }
  return map;
}

export function setMentionOptions(options: MentionOption[]): void {
  if (!options || options.length === 0) return;
  MENTION_OPTIONS.length = 0;
  MENTION_OPTIONS.push(...options);
  aliasMap = buildAliasMap(MENTION_OPTIONS);
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

export function getMentionSuggestions(query: string): MentionOption[] {
  if (!query) return MENTION_OPTIONS;
  const lower = query.toLowerCase();
  return MENTION_OPTIONS.filter((opt) =>
    opt.label.includes(query) ||
    opt.aliases.some((a) => a.toLowerCase().startsWith(lower)),
  );
}

export function findActiveMention(value: string, cursorIndex: number): {
  query: string;
  start: number;
} | null {
  const before = value.slice(0, cursorIndex);
  const match = before.match(/@([^\s@]*)$/);
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
): { newValue: string; newCursor: number } {
  const active = findActiveMention(value, cursorIndex);
  if (!active) return { newValue: value, newCursor: cursorIndex };
  const replacement = `@${option.label} `;
  const before = value.slice(0, active.start);
  const after = value.slice(cursorIndex);
  const newValue = before + replacement + after;
  const newCursor = before.length + replacement.length;
  return { newValue, newCursor };
}
