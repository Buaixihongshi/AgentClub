export interface DiscussCommand {
  skill: "discuss";
  goal: string;
}

/** Only a complete leading slash command invokes a project skill. */
export function parseDiscussCommand(input: string): DiscussCommand | null {
  const match = /^\/discuss(?:\s+|$)([\s\S]*)$/.exec(input.trim());
  return match ? { skill: "discuss", goal: match[1].trim() } : null;
}

export interface SkillCommandOption {
  /** 补全后插入正文的文本，如 "/discuss"。 */
  cmd: string;
  /** 展示标题。 */
  title: string;
  /** 说明文案。 */
  desc: string;
}

/**
 * `/` 候选命令。当前后端仅加载 discuss 技能（见 backend/app/domain/project_skills.py），
 * 因此只列出真实可用的命令，避免补全出后端无法识别的空指令。
 */
export const SKILL_COMMANDS: SkillCommandOption[] = [
  { cmd: "/discuss", title: "讨论", desc: "发起团队讨论（默认技能）" },
];

export function getSkillSuggestions(query: string): SkillCommandOption[] {
  if (!query) return SKILL_COMMANDS;
  const lower = query.toLowerCase();
  return SKILL_COMMANDS.filter(
    (s) => s.cmd.toLowerCase().includes(lower) || s.title.toLowerCase().includes(lower),
  );
}

/**
 * 找到光标处正在输入的 `/命令`，并校验触发边界：
 * `/` 只能出现在正文开头。讨论技能不支持指定单个成员（后端对 skill=discuss
 * 拒绝 target_agent_ids，见 backend/app/api/mission_requests.py），
 * 因此不再允许 "@成员 /discuss" 组合，避免补全出无法发送的指令。
 */
export function findActiveSkillCommand(
  value: string,
  cursorIndex: number,
): { query: string; start: number } | null {
  const before = value.slice(0, cursorIndex);
  const match = before.match(/\/([^\s/]*)$/);
  if (!match) return null;
  const start = before.length - match[0].length;
  const prefix = before.slice(0, start);
  if (prefix.trim() !== "") return null;
  return { query: match[1], start };
}

/**
 * 检测「@成员 /discuss」组合：/discuss 必须写在开头，点名写在命令之后
 * （/discuss @成员 议题），发送前明确拦截并提示，而不是退化成普通 goal。
 */
export function detectUnsupportedTargetedSkill(raw: string): boolean {
  return /^@[^\s@]+\s+\/discuss(?:\s|$)/.test(raw);
}

export function replaceActiveSkillCommand(
  value: string,
  cursorIndex: number,
  option: SkillCommandOption,
): { newValue: string; newCursor: number } {
  const active = findActiveSkillCommand(value, cursorIndex);
  if (!active) return { newValue: value, newCursor: cursorIndex };
  const replacement = `${option.cmd} `;
  const before = value.slice(0, active.start);
  const after = value.slice(cursorIndex);
  const newValue = before + replacement + after;
  const newCursor = before.length + replacement.length;
  return { newValue, newCursor };
}
