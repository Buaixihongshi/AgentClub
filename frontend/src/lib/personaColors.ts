import { mentionOptionById } from "./mentions";

export type PersonaColor = {
  border: string;
  name: string;
  avatar: string;
  accent: string;
};

export const PERSONA_COLORS: Record<string, PersonaColor> = {
  chulan: { border: "#D4AF37", name: "#8a6d10", avatar: "⚡", accent: "#D4AF37" },
  baobao: { border: "#4B5320", name: "#4B5320", avatar: "🧤", accent: "#4B5320" },
  wangye: { border: "#5D737E", name: "#3a4f59", avatar: "☯️", accent: "#5D737E" },
};

const FALLBACK: PersonaColor = {
  border: "#1A1A1A",
  name: "#1A1A1A",
  avatar: "👤",
  accent: "#9ca3af",
};

// User-created roles take a stable tone from the shared design tokens.
const ROLE_TONES: Array<Pick<PersonaColor, "border" | "name">> = [
  { border: "var(--sage)", name: "var(--sage-strong)" },
  { border: "var(--persona)", name: "var(--persona)" },
  { border: "var(--provider)", name: "var(--provider)" },
  { border: "var(--warning)", name: "var(--warning)" },
];

export function roleToneIndex(agentId: string): number {
  let hash = 0;
  for (const char of agentId) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % ROLE_TONES.length;
}

export function getPersonaColor(agentId?: string | null): PersonaColor {
  if (agentId && PERSONA_COLORS[agentId]) {
    return PERSONA_COLORS[agentId];
  }
  const option = agentId ? mentionOptionById(agentId) : undefined;
  if (agentId && option) {
    const tone = ROLE_TONES[roleToneIndex(agentId)];
    return { ...tone, accent: tone.border, avatar: option.label.slice(0, 1) || "👤" };
  }
  return FALLBACK;
}

/** 从姓名取一个代表字作为头像（"张楚岚"→"楚"，"王也"→"也"，"冯宝宝"→"宝"）。 */
export function avatarInitial(label: string): string {
  return label.charAt(label.length > 1 ? 1 : 0);
}
