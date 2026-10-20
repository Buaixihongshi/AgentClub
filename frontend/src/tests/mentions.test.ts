import { describe, expect, it } from "vitest";
import {
  MENTION_OPTIONS,
  collectMentionTargetIds,
  findActiveMention,
  getMentionSuggestions,
  parseMentionTarget,
  parseMentionTargets,
  replaceActiveMention,
  resolveMentionAlias,
  setMentionOptions,
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

  it("matches chulan by pinyin abbr 'zcl'", () => {
    const results = getMentionSuggestions("zcl");
    expect(results).toHaveLength(1);
    expect(results[0].id).toBe("chulan");
  });

  it("matches wangye by pinyin abbr 'wy'", () => {
    const results = getMentionSuggestions("wy");
    expect(results).toHaveLength(1);
    expect(results[0].id).toBe("wangye");
  });

  it("matches baobao by pinyin abbr 'fbb'", () => {
    const results = getMentionSuggestions("fbb");
    expect(results).toHaveLength(1);
    expect(results[0].id).toBe("baobao");
  });

  it("matches chulan by pinyin prefix 'zhang'", () => {
    const results = getMentionSuggestions("zhang");
    expect(results).toHaveLength(1);
    expect(results[0].id).toBe("chulan");
  });

  it("ranks pinyin prefix above pinyin substring", () => {
    const original = [...MENTION_OPTIONS];
    try {
      setMentionOptions([
        { id: "a", label: "张楚岚", aliases: [], role: "r", pinyin: "zhangchulan", abbr: "zcl" },
        { id: "b", label: "老张", aliases: [], role: "r", pinyin: "laozhang", abbr: "lz" },
      ]);
      expect(getMentionSuggestions("zhang").map((o) => o.id)).toEqual(["a", "b"]);
    } finally {
      setMentionOptions(original);
    }
  });

  it("ranks exact full name above name prefix regardless of candidate order", () => {
    const original = [...MENTION_OPTIONS];
    try {
      // 反序候选：前缀命中的「张楚岚王」排在前，全名「张楚岚」排在后。
      setMentionOptions([
        { id: "prefix", label: "张楚岚王", aliases: [], role: "r" },
        { id: "full", label: "张楚岚", aliases: [], role: "r" },
      ]);
      expect(getMentionSuggestions("张楚岚").map((o) => o.id)).toEqual(["full", "prefix"]);
    } finally {
      setMentionOptions(original);
    }
  });
});

describe("setMentionOptions", () => {
  it("backfills pinyin/abbr/color from builtin defaults by id when API omits them", () => {
    const original = [...MENTION_OPTIONS];
    try {
      setMentionOptions([
        { id: "chulan", label: "张楚岚", aliases: ["张楚岚", "chulan"], role: "首席架构师和核心开发者" },
        { id: "baobao", label: "冯宝宝", aliases: [], role: "代码审查员和安全专家" },
      ]);
      expect(getMentionSuggestions("zcl").map((o) => o.id)).toEqual(["chulan"]);
      expect(getMentionSuggestions("fbb").map((o) => o.id)).toEqual(["baobao"]);
      const chulan = MENTION_OPTIONS.find((o) => o.id === "chulan");
      expect(chulan?.color).toBe("#2563eb");
      // 保留 API 下发的真实 role，不被内置默认值覆盖。
      expect(chulan?.role).toBe("首席架构师和核心开发者");
    } finally {
      setMentionOptions(original);
    }
  });

  it("leaves unknown ids untouched", () => {
    const original = [...MENTION_OPTIONS];
    try {
      setMentionOptions([{ id: "custom", label: "自定", aliases: [], role: "r" }]);
      expect(MENTION_OPTIONS).toEqual([
        { id: "custom", label: "自定", aliases: [], role: "r" },
      ]);
    } finally {
      setMentionOptions(original);
    }
  });
});

describe("resolveMentionAlias", () => {
  it("resolves full name, latin alias and pinyin abbr", () => {
    expect(resolveMentionAlias("张楚岚")?.id).toBe("chulan");
    expect(resolveMentionAlias("Wangye")?.id).toBe("wangye");
    expect(resolveMentionAlias("zcl")?.id).toBe("chulan");
    expect(resolveMentionAlias("fbb")?.id).toBe("baobao");
  });

  it("returns undefined for unknown alias", () => {
    expect(resolveMentionAlias("unknown")).toBeUndefined();
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

  it("does not capture a trailing slash as part of the mention query", () => {
    expect(findActiveMention("@王也/", 4)).toBeNull();
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
