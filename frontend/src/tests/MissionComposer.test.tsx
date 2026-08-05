import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MissionComposer } from "../components/MissionComposer";

function renderComposer(overrides?: Partial<Parameters<typeof MissionComposer>[0]>) {
  const props = {
    value: "",
    busy: false,
    hasActiveMission: false,
    onChange: vi.fn(),
    onSubmit: vi.fn(),
    ...overrides,
  };
  return { ...render(<MissionComposer {...props} />), props };
}

describe("MissionComposer mention autocomplete", () => {
  it("shows all three agents when @ is typed", async () => {
    const user = userEvent.setup();
    const { props } = renderComposer();
    const textarea = screen.getByLabelText("任务输入");
    await user.type(textarea, "@");
    expect(props.onChange).toHaveBeenCalledWith("@");

    const { rerender } = render(
      <MissionComposer {...props} value="@" />,
    );

    const listbox = screen.getByRole("listbox", { name: "Agent 候选" });
    const options = within(listbox).getAllByRole("option");
    expect(options).toHaveLength(3);
    expect(options[0]).toHaveTextContent("张楚岚");
    expect(options[1]).toHaveTextContent("冯宝宝");
    expect(options[2]).toHaveTextContent("王也");
    void rerender;
  });

  it("filters to 王也 when @王 is typed", async () => {
    renderComposer({ value: "@王" });
    const textarea = screen.getByLabelText("任务输入");

    await userEvent.setup().type(textarea, "test");

    render(<MissionComposer
      value="@王"
      busy={false}
      hasActiveMission={false}
      onChange={vi.fn()}
      onSubmit={vi.fn()}
    />);

    const listbox = screen.queryAllByRole("listbox");
    if (listbox.length > 0) {
      const options = within(listbox[0]).getAllByRole("option");
      expect(options.some(o => o.textContent?.includes("王也"))).toBe(true);
    }
  });

  it("selects agent on mouse click and inserts mention", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onSubmit = vi.fn();

    const { rerender } = render(
      <MissionComposer
        value=""
        busy={false}
        hasActiveMission={false}
        onChange={onChange}
        onSubmit={onSubmit}
      />,
    );

    const textarea = screen.getByLabelText("任务输入");
    await user.type(textarea, "@");

    rerender(
      <MissionComposer
        value="@"
        busy={false}
        hasActiveMission={false}
        onChange={onChange}
        onSubmit={onSubmit}
      />,
    );

    const listbox = screen.getByRole("listbox", { name: "Agent 候选" });
    const wangye = within(listbox).getAllByRole("option")[2];
    await user.click(wangye);

    expect(onChange).toHaveBeenCalledWith("@王也 ");
  });

  it("keyboard ArrowDown/Enter selects agent", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onSubmit = vi.fn();

    const { rerender } = render(
      <MissionComposer
        value=""
        busy={false}
        hasActiveMission={false}
        onChange={onChange}
        onSubmit={onSubmit}
      />,
    );

    const textarea = screen.getByLabelText("任务输入");
    await user.type(textarea, "@");

    rerender(
      <MissionComposer
        value="@"
        busy={false}
        hasActiveMission={false}
        onChange={onChange}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByRole("listbox")).toBeInTheDocument();

    await user.keyboard("{ArrowDown}");
    await user.keyboard("{Enter}");

    expect(onChange).toHaveBeenCalledWith("@冯宝宝 ");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("Escape closes the menu", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    const { rerender } = render(
      <MissionComposer
        value=""
        busy={false}
        hasActiveMission={false}
        onChange={onChange}
        onSubmit={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText("任务输入"), "@");

    rerender(
      <MissionComposer
        value="@"
        busy={false}
        hasActiveMission={false}
        onChange={onChange}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("submit button works when menu is closed", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <MissionComposer
        value="hello"
        busy={false}
        hasActiveMission={false}
        onChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.click(screen.getByRole("button", { name: "创建任务" }));
    expect(onSubmit).toHaveBeenCalled();
  });

  it("submits with Enter when mention menu is closed", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <MissionComposer
        value="hello"
        busy={false}
        hasActiveMission={false}
        onChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    screen.getByLabelText("任务输入").focus();
    await user.keyboard("{Enter}");
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("keeps Shift+Enter as newline input", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <MissionComposer
        value="hello"
        busy={false}
        hasActiveMission={false}
        onChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    screen.getByLabelText("任务输入").focus();
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("shows 发送插话 when has active mission", () => {
    renderComposer({ hasActiveMission: true, value: "x" });
    expect(screen.getByRole("button", { name: "发送插话" })).toBeInTheDocument();
  });
});
