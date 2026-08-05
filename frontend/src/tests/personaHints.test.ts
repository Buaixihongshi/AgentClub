import { describe, expect, it } from "vitest";
import { parsePersonaHint } from "../lib/personaHints";

describe("persona hints", () => {
  it("extracts known persona mention and strips mention from content", () => {
    expect(parsePersonaHint("@王也 帮我从风险角度看")).toEqual({
      content: "帮我从风险角度看",
      targetAgentId: "wangye",
    });
  });

  it("does not create a provider binding", () => {
    const result = parsePersonaHint("@冯宝宝 直接做");

    expect(result).toEqual({
      content: "直接做",
      targetAgentId: "baobao",
    });
    expect(result).not.toHaveProperty("provider");
    expect(result).not.toHaveProperty("providerId");
    expect(result).not.toHaveProperty("provider_label");
  });

  it("does not treat partial latin aliases as persona mentions", () => {
    expect(parsePersonaHint("@wangyeplease review")).toEqual({
      content: "@wangyeplease review",
    });
  });
});
