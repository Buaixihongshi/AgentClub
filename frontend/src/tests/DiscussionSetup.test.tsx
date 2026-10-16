import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DiscussionSetup } from "../components/DiscussionSetup";

function renderSetup(overrides?: Partial<Parameters<typeof DiscussionSetup>[0]>) {
  const props = {
    mode: "roundtable" as const,
    participantIds: ["chulan", "baobao", "wangye"],
    onModeChange: vi.fn(),
    onToggleParticipant: vi.fn(),
    ...overrides,
  };
  return { ...render(<DiscussionSetup {...props} />), props };
}

describe("DiscussionSetup", () => {
  it("hides the participant picker in task mode", () => {
    renderSetup({ mode: "task" });
    const group = screen.getByRole("radiogroup", { name: "对话模式" });
    expect(within(group).getByRole("radio", { name: "任务" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.queryByRole("list", { name: "参与者" })).not.toBeInTheDocument();
    expect(screen.queryByText("通透机敏")).not.toBeInTheDocument();
  });

  it("lists all three personas with their personality tags selected by default", () => {
    renderSetup();
    const list = screen.getByRole("list", { name: "参与者" });
    const cards = within(list).getAllByRole("checkbox");
    expect(cards).toHaveLength(3);
    cards.forEach((card) => expect(card).toBeChecked());
    expect(within(list).getByText("张楚岚")).toBeInTheDocument();
    expect(within(list).getByText("通透机敏")).toBeInTheDocument();
    expect(within(list).getByText("冯宝宝")).toBeInTheDocument();
    expect(within(list).getByText("坦率好奇")).toBeInTheDocument();
    expect(within(list).getByText("王也")).toBeInTheDocument();
    expect(within(list).getByText("松弛笃定")).toBeInTheDocument();
  });

  it("never assigns a fixed job title to a persona", () => {
    renderSetup();
    const list = screen.getByRole("list", { name: "参与者" });
    ["主持", "执行者", "反对者", "总结人"].forEach((role) => {
      expect(within(list).queryByText(new RegExp(role))).not.toBeInTheDocument();
    });
  });

  it("toggles a participant through its checkbox", async () => {
    const user = userEvent.setup();
    const { props } = renderSetup();
    await user.click(screen.getByRole("checkbox", { name: /王也/ }));
    expect(props.onToggleParticipant).toHaveBeenCalledWith("wangye");
  });

  it("previews the default turn budget for the current selection", () => {
    renderSetup();
    expect(screen.getByText(/默认最多 7 个回合（含总结）/)).toBeInTheDocument();
  });

  it("switches the mode through the radio group", async () => {
    const user = userEvent.setup();
    const { props } = renderSetup({ mode: "task" });
    await user.click(screen.getByRole("radio", { name: "多人讨论" }));
    expect(props.onModeChange).toHaveBeenCalledWith("roundtable");
  });

  it("locks mode and participants while a discussion runs", () => {
    renderSetup({ locked: true });
    expect(screen.getByRole("radio", { name: "多人讨论" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "任务" })).toBeDisabled();
    screen
      .getAllByRole("checkbox")
      .forEach((box) => expect(box).toBeDisabled());
    expect(screen.getByRole("status")).toHaveTextContent("模式和参与者已锁定");
    expect(screen.queryByText(/默认最多 7 个回合/)).not.toBeInTheDocument();
  });

  it("surfaces the validation hint when fewer than two participants are selected", () => {
    renderSetup({ participantIds: ["chulan"], hint: "多人讨论至少要 2 位参与者，请再选一位。" });
    expect(screen.getByRole("alert")).toHaveTextContent("至少要 2 位参与者");
  });

  it("hides the validation hint once the discussion is locked", () => {
    renderSetup({
      participantIds: ["chulan"],
      locked: true,
      hint: "多人讨论至少要 2 位参与者，请再选一位。",
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
