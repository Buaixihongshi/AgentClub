import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConversationList } from "../components/ConversationList";
import type { ConversationSummary } from "../types";

const conversation: ConversationSummary = {
  id: "c_1",
  title: "需要归档的会话",
  status: "active",
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
  mission_count: 2,
};

function renderList(overrides: Partial<Parameters<typeof ConversationList>[0]> = {}) {
  const props: Parameters<typeof ConversationList>[0] = {
    conversations: [conversation],
    selectedId: undefined,
    onSelect: vi.fn(),
    onNew: vi.fn(),
    onRename: vi.fn(),
    scope: "active",
    onScopeChange: vi.fn(),
    onStatusChange: vi.fn(),
    ...overrides,
  };
  render(<ConversationList {...props} />);
  return props;
}

describe("ConversationList archive", () => {
  it("places the archive scope entry in the sidebar footer", () => {
    renderList();

    const list = screen.getByRole("navigation", { name: "会话列表" });
    const footer = list.querySelector(".conversation-list-footer");
    expect(footer).not.toBeNull();
    expect(footer).toContainElement(screen.getByRole("button", { name: "查看归档" }));
    expect(list.querySelector(".conversation-list-header .conv-scope-btn")).toBeNull();
  });

  it("archives an active conversation without deleting it", async () => {
    const props = renderList();

    await userEvent.click(screen.getByRole("button", { name: "归档会话" }));

    expect(props.onStatusChange).toHaveBeenCalledWith("c_1", "archived");
  });

  it("shows archived conversations and restores them", async () => {
    const props = renderList({
      scope: "archived",
      conversations: [{ ...conversation, status: "archived" }],
    });

    expect(screen.queryByRole("button", { name: "新对话" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "恢复会话" }));

    expect(props.onStatusChange).toHaveBeenCalledWith("c_1", "active");
  });

  it("uses the shared icon set instead of text glyphs for row actions", () => {
    renderList();

    for (const name of ["新对话", "重命名会话", "归档会话", "查看归档"]) {
      const button = screen.getByRole("button", { name });
      expect(button.querySelector("svg")).not.toBeNull();
      expect(button.textContent).not.toMatch(/[+⌑✎↩←]/);
    }
  });

  it("deletes a conversation only after inline confirmation", async () => {
    const onDelete = vi.fn();
    renderList({ onDelete });
    await userEvent.click(screen.getByRole("button", { name: "删除会话" }));
    expect(onDelete).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "删除会话" }));
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(onDelete).toHaveBeenCalledWith("c_1");
  });
});
