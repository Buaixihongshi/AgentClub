import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ThoughtBlock } from "../components/ThoughtBlock";

describe("ThoughtBlock", () => {
  it("renders nothing when both thoughts and tool calls are empty", () => {
    const { container } = render(<ThoughtBlock thoughts={[]} toolCalls={[]} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("defaults to collapsed and shows the thought count + preview", () => {
    render(
      <ThoughtBlock
        thoughts={[
          {
            content: "先判断",
            kind: "reasoning",
            createdAt: "2026-04-25T00:00:00Z",
          },
        ]}
      />,
    );

    const toggle = screen.getByRole("button", { name: "展开思考过程" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent("思考过程");
    expect(toggle).toHaveTextContent("· 1 条");
    expect(toggle).toHaveTextContent("先判断");
    expect(screen.queryByText("reasoning")).not.toBeInTheDocument();
  });

  it("toggles expanded state and renders plain thought entries", () => {
    render(
      <ThoughtBlock
        thoughts={[
          {
            content: "**不渲染 markdown**",
            kind: "plan",
            createdAt: "2026-04-25T00:00:00Z",
          },
          {
            content: "无 kind",
            createdAt: "2026-04-25T00:00:01Z",
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "展开思考过程" }));

    const toggle = screen.getByRole("button", { name: "折叠思考过程" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("plan")).toBeInTheDocument();
    expect(screen.getByText("**不渲染 markdown**")).toBeInTheDocument();
    expect(screen.getByText("无 kind")).toBeInTheDocument();
  });

  it("hides the 含工具 toggle when no tool calls are present", () => {
    render(
      <ThoughtBlock
        thoughts={[
          {
            content: "先判断",
            kind: "reasoning",
            createdAt: "2026-04-25T00:00:00Z",
          },
        ]}
        toolCalls={[]}
      />,
    );

    expect(screen.queryByRole("button", { name: /含工具/ })).toBeNull();
  });

  it("shows 含工具 toggle and merges tool calls by timestamp when enabled", () => {
    render(
      <ThoughtBlock
        thoughts={[
          {
            content: "先看一眼上下文",
            kind: "plan",
            createdAt: "2026-04-25T00:00:00Z",
          },
          {
            content: "决定调用工具",
            kind: "decision",
            createdAt: "2026-04-25T00:00:02Z",
          },
        ]}
        toolCalls={[
          {
            toolName: "read_context",
            summary: "scope=conversation",
            createdAt: "2026-04-25T00:00:01Z",
            eventType: "tool.call",
          },
        ]}
      />,
    );

    const toolsToggle = screen.getByRole("button", { name: /含工具 · 1/ });
    expect(toolsToggle).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(toolsToggle);

    expect(screen.getByRole("button", { name: "折叠思考过程" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );

    expect(screen.getByText("tool")).toBeInTheDocument();
    expect(screen.getByText("read_context")).toBeInTheDocument();
    expect(screen.getByText(/scope=conversation/)).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "纯思考" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    const items = screen
      .getAllByRole("listitem")
      .map((li) => li.textContent ?? "");
    expect(items).toHaveLength(3);
    expect(items[0]).toContain("先看一眼上下文");
    expect(items[1]).toContain("read_context");
    expect(items[2]).toContain("决定调用工具");
  });
});
