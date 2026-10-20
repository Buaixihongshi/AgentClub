import { describe, expect, it } from "vitest";
import {
  detectUnsupportedTargetedSkill,
  findActiveSkillCommand,
  getSkillSuggestions,
  parseDiscussCommand,
  replaceActiveSkillCommand,
  SKILL_COMMANDS,
} from "../lib/skillCommands";

describe("project skill commands", () => {
  it("recognizes a leading command with a full boundary and preserves multiline input", () => {
    expect(parseDiscussCommand("  /discuss\n方案比较\n约束：一周 ")).toEqual({ skill: "discuss", goal: "方案比较\n约束：一周" });
    expect(parseDiscussCommand("/discuss")).toEqual({ skill: "discuss", goal: "" });
  });
  it("does not turn quoted, embedded or similarly named text into a skill run", () => {
    for (const input of ["/discussfoo 问题", "解释 /discuss 的用法", "`/discuss 问题`", "> /discuss 问题", "讨论这个问题"])
      expect(parseDiscussCommand(input)).toBeNull();
  });
});

describe("getSkillSuggestions", () => {
  it("returns all commands for empty query", () => {
    expect(getSkillSuggestions("")).toEqual(SKILL_COMMANDS);
  });

  it("matches /discuss by command prefix or title", () => {
    expect(getSkillSuggestions("disc").map((s) => s.cmd)).toContain("/discuss");
    expect(getSkillSuggestions("讨").map((s) => s.cmd)).toContain("/discuss");
  });

  it("returns empty for unrelated query", () => {
    expect(getSkillSuggestions("xyz")).toHaveLength(0);
  });
});

describe("findActiveSkillCommand", () => {
  it("allows a leading slash at the start of input", () => {
    expect(findActiveSkillCommand("/disc", 5)).toEqual({ query: "disc", start: 0 });
    expect(findActiveSkillCommand("/", 1)).toEqual({ query: "", start: 0 });
  });

  it("does not allow a slash after a member mention (discuss has no member target)", () => {
    expect(findActiveSkillCommand("@张楚岚 /d", 7)).toBeNull();
    expect(findActiveSkillCommand("@王也/", 4)).toBeNull();
  });

  it("rejects a slash in URLs, paths or after plain text", () => {
    expect(findActiveSkillCommand("https://x/", 9)).toBeNull();
    expect(findActiveSkillCommand("网址 /disc", 6)).toBeNull();
    expect(findActiveSkillCommand("@张楚岚 之后 /disc", 11)).toBeNull();
  });

  it("rejects a slash after an unconfirmed alias", () => {
    expect(findActiveSkillCommand("@unknown /disc", 12)).toBeNull();
  });
});

describe("replaceActiveSkillCommand", () => {
  it("replaces a partial command with the full command", () => {
    const { newValue, newCursor } = replaceActiveSkillCommand("/disc", 5, SKILL_COMMANDS[0]);
    expect(newValue).toBe("/discuss ");
    expect(newCursor).toBe(9);
  });

  it("leaves text after a member mention unchanged (slash is not a command there)", () => {
    const { newValue, newCursor } = replaceActiveSkillCommand("@张楚岚 /d补充", 7, SKILL_COMMANDS[0]);
    expect(newValue).toBe("@张楚岚 /d补充");
    expect(newCursor).toBe(7);
  });

  it("returns unchanged when no active command", () => {
    const { newValue, newCursor } = replaceActiveSkillCommand("没有命令", 4, SKILL_COMMANDS[0]);
    expect(newValue).toBe("没有命令");
    expect(newCursor).toBe(4);
  });
});

describe("detectUnsupportedTargetedSkill", () => {
  it("flags the @成员 /discuss combination", () => {
    expect(detectUnsupportedTargetedSkill("@王也 /discuss 看下风险")).toBe(true);
    expect(detectUnsupportedTargetedSkill("@王也 /discuss")).toBe(true);
  });
  it("does not flag leading /discuss or plain text mentioning the command", () => {
    expect(detectUnsupportedTargetedSkill("/discuss 方案比较")).toBe(false);
    expect(detectUnsupportedTargetedSkill("@王也 解释一下 /discuss 的用法")).toBe(false);
    expect(detectUnsupportedTargetedSkill("看下 https://x/y")).toBe(false);
  });
});
