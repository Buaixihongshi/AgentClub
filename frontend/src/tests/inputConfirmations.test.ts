import { describe, expect, it } from "vitest";
import {
  applyEdit,
  confirmMention,
  confirmedMentionIds,
  stripConfirmedMentions,
} from "../lib/inputConfirmations";
import { MENTION_OPTIONS } from "../lib/mentions";

const wangye = MENTION_OPTIONS.find((o) => o.id === "wangye")!;
const chulan = MENTION_OPTIONS.find((o) => o.id === "chulan")!;

describe("applyEdit", () => {
  it("shifts a mention when text is inserted before it", () => {
    const m = { agentId: "wangye", text: "@王也 ", start: 0, end: 4 };
    expect(applyEdit("@王也 ", "看 @王也 ", [m])).toEqual([
      { agentId: "wangye", text: "@王也 ", start: 2, end: 6 },
    ]);
  });

  it("keeps a mention intact when text is appended after it", () => {
    const m = { agentId: "wangye", text: "@王也 ", start: 0, end: 4 };
    expect(applyEdit("@王也 ", "@王也 看风险", [m])).toEqual([m]);
  });

  it("invalidates a mention when its label is edited", () => {
    const m = { agentId: "wangye", text: "@王也 ", start: 0, end: 4 };
    expect(applyEdit("@王也 ", "@老王也 ", [m])).toEqual([]);
  });

  it("invalidates a mention when its trailing space is deleted", () => {
    const m = { agentId: "wangye", text: "@王也 ", start: 0, end: 4 };
    expect(applyEdit("@王也 ", "@王也", [m])).toEqual([]);
  });

  it("drops a mention overlapping a mid-text edit", () => {
    const m = { agentId: "wangye", text: "@王也 ", start: 2, end: 6 };
    expect(applyEdit("看 @王也 风险", "看 @老张 风险", [m])).toEqual([]);
  });
});

describe("confirmMention", () => {
  it("records the inserted @label range and shifts later mentions", () => {
    const prior = { agentId: "chulan", text: "@张楚岚 ", start: 0, end: 5 };
    const next = "@张楚岚 @王也 ";
    // 在 "@张楚岚 "（0..5）之后确认 "@王也"（替换自 start=5）。
    const result = confirmMention("@张楚岚 @王", next, 5, wangye, [prior]);
    expect(result).toEqual([
      { agentId: "chulan", text: "@张楚岚 ", start: 0, end: 5 },
      { agentId: "wangye", text: "@王也 ", start: 5, end: 9 },
    ]);
  });

  it("replaces an overlapping old mention on re-confirm", () => {
    const prior = { agentId: "chulan", text: "@张楚岚 ", start: 0, end: 5 };
    // 重新确认同一位置为 wangye（理论上应替换旧范围）。
    const result = confirmMention("@张楚岚 ", "@王也 ", 0, wangye, [prior]);
    expect(result).toEqual([{ agentId: "wangye", text: "@王也 ", start: 0, end: 4 }]);
  });
});

describe("confirmedMentionIds", () => {
  it("dedupes while keeping confirmation order", () => {
    expect(
      confirmedMentionIds([
        { agentId: "wangye", text: "@王也 ", start: 0, end: 4 },
        { agentId: "chulan", text: "@张楚岚 ", start: 4, end: 9 },
        { agentId: "wangye", text: "@王也 ", start: 9, end: 13 },
      ]),
    ).toEqual(["wangye", "chulan"]);
  });
});

describe("stripConfirmedMentions", () => {
  it("removes confirmed mention tokens and leaves surrounding text", () => {
    expect(
      stripConfirmedMentions("@王也 看风险", [
        { agentId: "wangye", text: "@王也 ", start: 0, end: 4 },
      ]),
    ).toBe("看风险");
  });

  it("leaves unconfirmed @别名 text in place", () => {
    expect(stripConfirmedMentions("@zcl 看风险", [])).toBe("@zcl 看风险");
  });
});
