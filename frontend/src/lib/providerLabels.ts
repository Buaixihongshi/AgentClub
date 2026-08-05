export function compactProviderLabel(label?: string | null): string {
  const value = (label ?? "").trim();
  const lower = value.toLowerCase();

  if (!value || lower.includes("pending")) return "Provider";
  if (lower.includes("claude")) return "Claude";
  if (lower.includes("codex")) return "Codex";
  if (lower.includes("gemini")) return "Gemini";
  if (lower.includes("antigravity")) return "Antigravity";
  if (lower.includes("fake")) return "演示";

  return value.replace(/\s+(CLI|Code)$/i, "");
}

export function compactPersonaProviderLabel(
  personaLabel?: string | null,
  providerLabel?: string | null,
): string {
  const persona = personaLabel?.trim() || "Agent";
  return `${persona}（${compactProviderLabel(providerLabel)}）`;
}
