import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChatBubble } from "../components/ChatBubble";
import type { TurnView } from "../types";

function makeTurn(overrides: Partial<TurnView>): TurnView {
  return {
    id: "live:turn_1",
    turnIndex: 1,
    role: "agent",
    eventType: "message.partial",
    content: "",
    capability: "unknown",
    providerLabel: "Gemini CLI",
    personaLabel: "王也",
    agentId: "wangye",
    createdAt: "2026-05-13T00:00:00Z",
    metadata: {},
    isStatusEvent: false,
    ...overrides,
  };
}

describe("ChatBubble streaming", () => {
  it("renders partialText with typing-dots when no final content", () => {
    const { container } = render(
      <ChatBubble turn={makeTurn({ partialText: "我正在" })} />,
    );
    expect(screen.getByText("我正在")).toBeInTheDocument();
    expect(container.querySelector(".typing-dots")).toBeTruthy();
  });

  it("renders final content (not partialText) when both present", () => {
    const { container } = render(
      <ChatBubble
        turn={makeTurn({ content: "我想完了", partialText: "我正在" })}
      />,
    );
    expect(screen.getByText("我想完了")).toBeInTheDocument();
    expect(screen.queryByText("我正在")).not.toBeInTheDocument();
    expect(container.querySelector(".typing-dots")).toBeFalsy();
  });

  it("does not render typing-dots for plain message events without content or partialText", () => {
    const { container } = render(
      <ChatBubble turn={makeTurn({ eventType: "message" })} />,
    );
    expect(container.querySelector(".typing-dots")).toBeFalsy();
  });

  it("renders typing-dots for in-flight message.partial bubble even when partialText is empty", () => {
    const { container } = render(
      <ChatBubble turn={makeTurn({ eventType: "message.partial", partialText: "" })} />,
    );
    expect(container.querySelector(".typing-dots")).toBeTruthy();
  });

  it("labels empty Codex streaming bubbles as waiting for a complete reply", () => {
    render(
      <ChatBubble
        turn={makeTurn({
          providerLabel: "Codex CLI",
          personaLabel: "冯宝宝",
          partialText: "",
        })}
      />,
    );
    expect(screen.getByText("等待完整回复")).toBeInTheDocument();
  });

  it("does not label empty Claude streaming bubbles as streaming output", () => {
    render(
      <ChatBubble
        turn={makeTurn({
          providerLabel: "Claude Code",
          personaLabel: "张楚岚",
          partialText: "",
        })}
      />,
    );
    expect(screen.queryByText("正在流式输出")).not.toBeInTheDocument();
    expect(screen.getByText("等待完整回复")).toBeInTheDocument();
  });
});
