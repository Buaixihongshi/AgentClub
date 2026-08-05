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

export function getPersonaColor(agentId?: string | null): PersonaColor {
  if (agentId && PERSONA_COLORS[agentId]) {
    return PERSONA_COLORS[agentId];
  }
  return FALLBACK;
}
