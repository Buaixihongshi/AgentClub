import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SettingsButton } from "../components/SettingsButton";
import type { HubSettingsView } from "../types";

const mockView: HubSettingsView = {
  runtime: {
    use_fake_cli: { value: true, source: "default" },
    max_turns: { value: 6, source: "default" },
    max_a2a_invocations: { value: 8, source: "default" },
    cli_timeout: { value: 120, source: "default" },
  },
  providers: [
    { id: "fake", label: "Fake", enabled: { value: true, source: "committed" } },
  ],
  agents: [],
};

describe("SettingsButton", () => {
  it("clicking the button opens modal", () => {
    render(<SettingsButton view={mockView} onSave={vi.fn()} />);
    expect(screen.queryByText("设置")).toBeNull();
    fireEvent.click(screen.getByTestId("settings-button"));
    expect(screen.getByRole("heading", { name: "设置" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "团队配置" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "团队配置" }));
    expect(screen.getByRole("heading", { name: "团队配置" })).toBeTruthy();
  });

  it("closing modal returns focus to button", () => {
    vi.useFakeTimers();
    render(<SettingsButton view={mockView} onSave={vi.fn()} />);
    const button = screen.getByTestId("settings-button");
    fireEvent.click(button);
    expect(screen.getByRole("heading", { name: "设置" })).toBeTruthy();
    fireEvent.click(screen.getByTestId("settings-close"));
    expect(screen.queryByRole("heading", { name: "设置" })).toBeNull();
    vi.runAllTimers();
    expect(document.activeElement).toBe(button);
    vi.useRealTimers();
  });
});
