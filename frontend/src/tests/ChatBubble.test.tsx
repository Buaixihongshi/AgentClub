import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChatBubble } from "../components/ChatBubble";
import type { TurnView } from "../types";

function makeTurn(overrides: Partial<TurnView>): TurnView {
  return {
    id: "t1",
    turnIndex: 1,
    role: "agent",
    eventType: "message",
    content: "测试内容",
    capability: "planning",
    providerLabel: "Claude Code",
    personaLabel: "张楚岚",
    agentId: "chulan",
    createdAt: "2026-04-25T00:00:00Z",
    metadata: {},
    isStatusEvent: false,
    ...overrides,
  };
}

describe("ChatBubble", () => {
  it("renders user bubble right-aligned with boss label", () => {
    const turn = makeTurn({
      role: "user",
      content: "帮我分析",
      personaLabel: undefined,
      agentId: null,
    });
    const { container } = render(<ChatBubble turn={turn} />);
    expect(screen.getByText("老板")).toBeInTheDocument();
    expect(screen.getByText("帮我分析")).toBeInTheDocument();
    expect(container.querySelector(".chat-bubble-user")).toBeTruthy();
  });

  it("renders agent bubble with compact persona and provider identity", () => {
    render(<ChatBubble turn={makeTurn({})} />);
    expect(screen.getByText("张楚岚（Claude）")).toBeInTheDocument();
    expect(screen.queryByText("规划")).not.toBeInTheDocument();
    expect(screen.queryByText("via Claude Code")).not.toBeInTheDocument();
    expect(screen.getByText("测试内容")).toBeInTheDocument();
  });

  it("applies gold border for chulan", () => {
    const { container } = render(
      <ChatBubble turn={makeTurn({ agentId: "chulan" })} />,
    );
    expect(container.querySelector(".chat-bubble-agent")).toHaveStyle({
      borderLeftColor: "#D4AF37",
    });
  });

  it("applies olive border for baobao", () => {
    const { container } = render(
      <ChatBubble
        turn={makeTurn({
          agentId: "baobao",
          personaLabel: "冯宝宝",
          capability: "implementation",
        })}
      />,
    );
    expect(container.querySelector(".chat-bubble-agent")).toHaveStyle({
      borderLeftColor: "#4B5320",
    });
  });

  it("applies slate-blue border for wangye", () => {
    const { container } = render(
      <ChatBubble
        turn={makeTurn({
          agentId: "wangye",
          personaLabel: "王也",
          capability: "review",
        })}
      />,
    );
    expect(container.querySelector(".chat-bubble-agent")).toHaveStyle({
      borderLeftColor: "#5D737E",
    });
  });

  it("hides role hint for known capabilities", () => {
    render(
      <ChatBubble turn={makeTurn({ capability: "review", personaLabel: "王也" })} />,
    );
    expect(screen.queryByText("分析")).not.toBeInTheDocument();
  });

  it("hides role hint for unknown capability", () => {
    render(<ChatBubble turn={makeTurn({ capability: "unknown" })} />);
    expect(screen.queryByText("规划")).not.toBeInTheDocument();
    expect(screen.queryByText("执行")).not.toBeInTheDocument();
    expect(screen.queryByText("分析")).not.toBeInTheDocument();
  });

  it("shows turn index for agent bubbles", () => {
    render(<ChatBubble turn={makeTurn({ turnIndex: 2 })} />);
    expect(screen.getByText("第 2 轮")).toBeInTheDocument();
  });

  it("renders markdown emphasis as <strong> instead of literal asterisks", () => {
    const { container } = render(
      <ChatBubble turn={makeTurn({ content: "这是 **关键** 信息" })} />,
    );
    const strong = container.querySelector("strong");
    expect(strong).toBeTruthy();
    expect(strong?.textContent).toBe("关键");
    expect(container.textContent).not.toContain("**");
  });

  it("renders markdown lists in user bubble", () => {
    const { container } = render(
      <ChatBubble
        turn={makeTurn({
          role: "user",
          content: "- 第一条\n- 第二条",
          personaLabel: undefined,
          agentId: null,
        })}
      />,
    );
    const items = container.querySelectorAll("li");
    expect(items.length).toBe(2);
    expect(items[0].textContent).toBe("第一条");
    expect(items[1].textContent).toBe("第二条");
  });

  it("renders inline code as <code>", () => {
    const { container } = render(
      <ChatBubble turn={makeTurn({ content: "调用 `read_artifact` 工具" })} />,
    );
    const code = container.querySelector("code");
    expect(code).toBeTruthy();
    expect(code?.textContent).toBe("read_artifact");
  });

  it("shows compact persona, provider, and turn number for agent messages", () => {
    render(
      <ChatBubble
        turn={{
          id: "a1",
          turnIndex: 2,
          role: "agent",
          eventType: "message",
          content: "我先把实现边界收一下。",
          capability: "planning",
          providerLabel: "Gemini CLI",
          personaLabel: "张楚岚",
          agentId: "chulan",
          createdAt: "2026-04-30T01:00:00Z",
          metadata: {},
          isStatusEvent: false,
        }}
      />,
    );

    expect(screen.getByText("张楚岚（Gemini）")).toBeInTheDocument();
    expect(screen.queryByText("规划")).not.toBeInTheDocument();
    expect(screen.queryByText("via Gemini CLI")).not.toBeInTheDocument();
    expect(screen.getByText("第 2 轮")).toBeInTheDocument();
  });
});
