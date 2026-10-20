export interface DiscussCommand {
  skill: "discuss";
  goal: string;
}

/** Only a complete leading slash command invokes a project skill. */
export function parseDiscussCommand(input: string): DiscussCommand | null {
  const match = /^\/discuss(?:\s+|$)([\s\S]*)$/.exec(input.trim());
  return match ? { skill: "discuss", goal: match[1].trim() } : null;
}
