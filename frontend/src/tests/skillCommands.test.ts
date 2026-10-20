import { describe, expect, it } from "vitest";
import { parseDiscussCommand } from "../lib/skillCommands";

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
