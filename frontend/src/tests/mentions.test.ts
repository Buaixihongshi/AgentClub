import { describe, expect, it } from "vitest";
import {
  MENTION_OPTIONS,
  collectMentionTargetIds,
  findActiveMention,
  getMentionSuggestions,
  parseMentionTarget,
  parseMentionTargets,
  replaceActiveMention,
} from "../lib/mentions";

describe("parseMentionTarget", () => {
  it("extracts @王也 as wangye", () => {
    expect(parseMentionTarget("@王也 看风险")).toEqual({
      content: "看风险",
      targetAgentId: "wangye",
    });
  });

  it("extracts @冯宝宝 as baobao", () => {
    expect(parseMentionTarget("@冯宝宝 直接做")).toEqual({
      content: "直接做",
      targetAgentId: "baobao",
    });
  });

  it("extracts @宝宝 as baobao", () => {
    expect(parseMentionTarget("@宝宝 补一刀")).toEqual({
      content: "补一刀",
      targetAgentId: "baobao",
    });
  });

  it("extracts @张楚岚 as chulan", () => {
    expect(parseMentionTarget("@张楚岚 规划")).toEqual({
      content: "规划",
      targetAgentId: "chulan",
    });
  });

  it("extracts latin aliases case-insensitively", () => {
    expect(parseMentionTarget("@Wangye review risk")).toEqual({
      content: "review risk",
      targetAgentId: "wangye",
    });
    expect(parseMentionTarget("@Baobao go")).toEqual({
      content: "go",
      targetAgentId: "baobao",
    });
  });

  it("does not treat partial latin aliases as mentions", () => {
    expect(parseMentionTarget("@wangyeplease review")).toEqual({
      content: "@wangyeplease review",
    });
  });

  it("returns full content when no mention matches", () => {
    expect(parseMentionTarget("没有 mention")).toEqual({
      content: "没有 mention",
    });
  });

  it("handles mention-only input", () => {
    expect(parseMentionTarget("@老王")).toEqual({
      content: "",
      targetAgentId: "wangye",
    });
  });

  it("does not bind to provider or model", () => {
    const result = parseMentionTarget("@冯宝宝 做");
    expect(result).not.toHaveProperty("provider");
    expect(result).not.toHaveProperty("providerId");
  });
});

describe("parseMentionTargets (multi)", () => {
  it("extracts two leading mentions and preserves order", () => {
    expect(parseMentionTargets("@张楚岚 @冯宝宝 你俩讨论下")).toEqual({
      content: "你俩讨论下",
      targetAgentIds: ["chulan", "baobao"],
    });
  });

  it("extracts three leading mentions", () => {
    expect(parseMentionTargets("@张楚岚 @冯宝宝 @王也 三人各说一句")).toEqual({
      content: "三人各说一句",
      targetAgentIds: ["chulan", "baobao", "wangye"],
    });
  });

  it("dedupes repeated mentions while keeping first-seen order", () => {
    expect(parseMentionTargets("@chulan @baobao @chulan 重复点名")).toEqual({
      content: "重复点名",
      targetAgentIds: ["chulan", "baobao"],
    });
  });

  it("stops at the first non-alias and keeps remainder as content", () => {
    expect(parseMentionTargets("@chulan @unknown rest text")).toEqual({
      content: "@unknown rest text",
      targetAgentIds: ["chulan"],
    });
  });

  it("returns empty ids list when no leading mention", () => {
    expect(parseMentionTargets("no mention here")).toEqual({
      content: "no mention here",
      targetAgentIds: [],
    });
  });
});

describe("collectMentionTargetIds", () => {
  it("extracts mentions from the whole mission text and preserves first-seen order", () => {
    expect(collectMentionTargetIds("依据经验复盘@张楚岚 @冯宝宝 @王也")).toEqual([
      "chulan",
      "baobao",
      "wangye",
    ]);
  });

  it("ignores unknown and partial aliases while deduping known mentions", () => {
    expect(collectMentionTargetIds("找 @王也 和 @unknown，再找 @wangyeplease，最后 @王也")).toEqual([
      "wangye",
    ]);
  });
});

describe("getMentionSuggestions", () => {
  it("returns all options for empty query", () => {
    expect(getMentionSuggestions("")).toEqual(MENTION_OPTIONS);
  });

  it("filters to 王也 for query '王'", () => {
    const results = getMentionSuggestions("王");
    expect(results).toHaveLength(1);
    expect(results[0].id).toBe("wangye");
  });

  it("filters to baobao for query 'bao'", () => {
    const results = getMentionSuggestions("bao");
    expect(results).toHaveLength(1);
    expect(results[0].id).toBe("baobao");
  });

  it("matches 宝宝 for query '宝'", () => {
    const results = getMentionSuggestions("宝");
    expect(results).toHaveLength(1);
    expect(results[0].id).toBe("baobao");
  });

  it("returns empty for unrelated query", () => {
    expect(getMentionSuggestions("xyz")).toHaveLength(0);
  });
});

describe("findActiveMention", () => {
  it("finds active mention at cursor after @", () => {
    const result = findActiveMention("hello @王", 9);
    expect(result).toEqual({ query: "王", start: 6 });
  });

  it("returns null when no @ present", () => {
    expect(findActiveMention("no mention", 10)).toBeNull();
  });

  it("finds mention with empty query (just @)", () => {
    const result = findActiveMention("test @", 6);
    expect(result).toEqual({ query: "", start: 5 });
  });
});

describe("replaceActiveMention", () => {
  it("replaces partial mention with full label", () => {
    const { newValue, newCursor } = replaceActiveMention(
      "hello @王",
      9,
      MENTION_OPTIONS[2],
    );
    expect(newValue).toBe("hello @王也 ");
    expect(newCursor).toBe(10);
  });

  it("preserves text after cursor", () => {
    const { newValue } = replaceActiveMention(
      "@bao extra",
      4,
      MENTION_OPTIONS[1],
    );
    expect(newValue).toBe("@冯宝宝  extra");
  });

  it("returns unchanged if no active mention", () => {
    const { newValue, newCursor } = replaceActiveMention(
      "no mention",
      10,
      MENTION_OPTIONS[0],
    );
    expect(newValue).toBe("no mention");
    expect(newCursor).toBe(10);
  });
});
