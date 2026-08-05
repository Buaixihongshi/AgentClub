import { describe, expect, it } from "vitest";
import { getPersonaColor, PERSONA_COLORS } from "../lib/personaColors";

describe("personaColors", () => {
  it("returns gold for chulan", () => {
    expect(getPersonaColor("chulan")).toEqual(PERSONA_COLORS.chulan);
  });

  it("returns olive for baobao", () => {
    expect(getPersonaColor("baobao")).toEqual(PERSONA_COLORS.baobao);
  });

  it("returns slate-blue for wangye", () => {
    expect(getPersonaColor("wangye")).toEqual(PERSONA_COLORS.wangye);
  });

  it("returns fallback ink for unknown agentId", () => {
    const color = getPersonaColor("unknown-agent");
    expect(color.border).toBe("#1A1A1A");
  });

  it("returns fallback ink for null/undefined", () => {
    expect(getPersonaColor(null)).toEqual(getPersonaColor(undefined));
  });
});
