import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../styles.css"), "utf-8");

function rule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "m"));
  return match?.[1] ?? "";
}

describe("critical UI CSS contracts", () => {
  it("right-aligns provider versions in the side rail", () => {
    const providerVersion = rule(".provider-status-version");

    expect(providerVersion).toContain("text-align: right");
    expect(providerVersion).toContain("margin-left: auto");
  });

  it("uses a light user message bubble instead of a black bubble", () => {
    const userBubble = rule(".chat-bubble-user");

    expect(userBubble).toContain("background: var(--sage-soft)");
    expect(userBubble).toContain("color: var(--ink)");
    expect(userBubble).not.toContain("background: var(--ink)");
    expect(userBubble).not.toContain("color: var(--paper)");
  });
});
