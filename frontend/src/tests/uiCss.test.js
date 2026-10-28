import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../styles.css"), "utf-8");
const componentDir = resolve(__dirname, "../components");
const componentCss = readdirSync(componentDir)
  .filter((name) => name.endsWith(".css"))
  .map((name) => readFileSync(resolve(componentDir, name), "utf-8"));
const allCss = [css, ...componentCss].join("\n");

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

  it("only references design tokens that are defined", () => {
    const defined = new Set([...allCss.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
    const referenced = new Set([...allCss.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]));
    const missing = [...referenced].filter(
      (name) => !defined.has(name) && !name.startsWith("--radix-") && name !== "--flow-node-width",
    );

    expect(missing).toEqual([]);
  });

  it("defines the screen-reader-only utility used by visually hidden labels", () => {
    const srOnly = rule(".sr-only");

    expect(srOnly).toContain("position: absolute");
    expect(srOnly).toContain("clip: rect(0, 0, 0, 0)");
  });

  it("keeps the light-only theme consistent instead of darkening single components", () => {
    expect(rule(":root")).toContain("color-scheme: light");
    expect(allCss).not.toMatch(/prefers-color-scheme:\s*dark/);
  });

  it("frames the settings modal like the other dialogs", () => {
    const modal = rule(".settings-modal");

    expect(modal).toContain("border: 2px solid var(--ink)");
    expect(modal).toContain("box-shadow: var(--hard-shadow)");
  });
});
