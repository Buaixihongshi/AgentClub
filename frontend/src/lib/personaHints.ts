import { parseMentionTarget } from "./mentions";

export function parsePersonaHint(raw: string): {
  content: string;
  targetAgentId?: string;
} {
  return parseMentionTarget(raw);
}
