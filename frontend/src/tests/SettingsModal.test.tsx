import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SettingsModal } from "../components/SettingsModal";

vi.mock("../lib/api", () => ({
  fetchObservabilityOverview: vi.fn().mockResolvedValue({
    generated_at: "2026-10-20T10:00:00+00:00",
    window: "all",
    totals: { conversations: 0, missions: 0, turns: 0, tool_calls: 0, native_calls: 0, tokens: 0, cost: 0 },
    checks: [],
    turns_passed: 0,
    coverage: { turns: 0, native_calls: 0, native_rate: null, tool_calls: 0, output_rate: null, cost_known_rate: null },
    spend: { total: 0, reported: 0, estimated: 0, unknown_turns: 0, by_provider: [] },
    by_agent: [],
    by_provider: [],
    daily: [],
    recent_turns: [],
    failures: [],
  }),
}));
import type { HubSettingsView, HubSettingsPatch } from "../types";

const baseView: HubSettingsView = {
  runtime: {
    use_fake_cli: { value: true, source: "default" },
    max_turns: { value: 6, source: "default" },
    max_a2a_invocations: { value: 8, source: "default" },
    cli_timeout: { value: 120, source: "default" },
    a2a_idle_timeout: { value: 90, source: "default" },
  },
  providers: [
    { id: "fake", label: "Fake", enabled: { value: true, source: "committed" } },
    { id: "codex", label: "Codex CLI", enabled: { value: true, source: "user" } },
    { id: "claude", label: "Claude", enabled: { value: false, source: "committed" } },
    { id: "gemini", label: "Gemini", enabled: { value: false, source: "committed" } },
    { id: "antigravity", label: "Antigravity", enabled: { value: false, source: "committed" } },
  ],
  agents: [
    {
      id: "wangye",
      display_name: "王也",
      default_provider_id: { value: "antigravity", source: "committed" },
      fallback_provider_ids: { value: ["claude"], source: "committed" },
      base_default_provider_id: "antigravity",
      base_fallback_provider_ids: ["claude"],
    },
  ],
};

describe("SettingsModal", () => {
  it("opens the global observability tab without a save bar", async () => {
    render(<SettingsModal view={baseView} onSave={vi.fn()} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "观测" }));
    expect(await screen.findByRole("heading", { name: "观测" })).toBeInTheDocument();
    expect(screen.getByText(/全部会话/)).toBeInTheDocument();
    expect(screen.queryByTestId("settings-save")).not.toBeInTheDocument();
  });

  it("hides internal and retired providers from the provider list", () => {
    render(<SettingsModal view={baseView} onSave={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getAllByText("Claude").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("Fake")).not.toBeInTheDocument();
    expect(screen.queryByText("Gemini")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "团队配置" }));
    expect(screen.getAllByText("演示模式（内置）").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("committed").length).toBeGreaterThanOrEqual(1);
  });

  it("toggling a provider builds correct patch", () => {
    const onSave = vi.fn();
    render(<SettingsModal view={baseView} onSave={onSave} onClose={vi.fn()} />);
    const claudeToggle = screen.getByTestId("toggle-claude");
    fireEvent.click(claudeToggle);
    const saveBtn = screen.getByTestId("settings-save");
    fireEvent.click(saveBtn);
    const patch: HubSettingsPatch = onSave.mock.calls[0][0];
    expect(patch.providers).toBeDefined();
    expect(patch.providers!["claude"]).toEqual({ enabled: true });
    expect(patch.providers!["fake"]).toBeUndefined();
  });

  it("runtime fields edit produces patch with only changed keys", () => {
    const onSave = vi.fn();
    render(<SettingsModal view={baseView} onSave={onSave} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "运行参数" }));
    const maxTurnsInput = screen.getByTestId("input-max_turns");
    fireEvent.change(maxTurnsInput, { target: { value: "10" } });
    const saveBtn = screen.getByTestId("settings-save");
    fireEvent.click(saveBtn);
    const patch: HubSettingsPatch = onSave.mock.calls[0][0];
    expect(patch.runtime).toBeDefined();
    expect(patch.runtime!.max_turns).toBe(10);
    expect(patch.runtime!.cli_timeout).toBeUndefined();
  });

  it("shows cost warning when enabling a real provider", () => {
    render(<SettingsModal view={baseView} onSave={vi.fn()} onClose={vi.fn()} />);
    const claudeToggle = screen.getByTestId("toggle-claude");
    fireEvent.click(claudeToggle);
    expect(screen.getByText(/产生费用/)).toBeTruthy();
  });

  it("disables save button when no diff", () => {
    render(<SettingsModal view={baseView} onSave={vi.fn()} onClose={vi.fn()} />);
    const saveBtn = screen.getByTestId("settings-save");
    expect(saveBtn).toHaveProperty("disabled", true);
  });

  it("changing agent default provider builds agents patch", () => {
    const onSave = vi.fn();
    render(<SettingsModal view={baseView} onSave={onSave} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "团队配置" }));
    const select = screen.getByTestId("agent-default-wangye") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "claude" } });
    fireEvent.click(screen.getByTestId("settings-save"));
    const patch: HubSettingsPatch = onSave.mock.calls[0][0];
    expect(patch.agents).toBeDefined();
    expect(patch.agents!["wangye"]).toEqual({ default_provider_id: "claude" });
  });

  it("toggling agent fallback updates the list", () => {
    const onSave = vi.fn();
    render(<SettingsModal view={baseView} onSave={onSave} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "团队配置" }));
    // The internal demo provider remains available as a safe route fallback,
    // but is no longer exposed as "Fake CLI".
    fireEvent.click(screen.getByTestId("agent-fallback-wangye-fake"));
    fireEvent.click(screen.getByTestId("settings-save"));
    const patch: HubSettingsPatch = onSave.mock.calls[0][0];
    expect(patch.agents!["wangye"].fallback_provider_ids).toEqual(["claude", "fake"]);
  });

  it("reset button only appears when user override is active", () => {
    const overriddenView: HubSettingsView = {
      ...baseView,
      agents: [
        {
          id: "wangye",
          display_name: "王也",
          default_provider_id: { value: "claude", source: "user" },
          fallback_provider_ids: { value: [], source: "user" },
          base_default_provider_id: "antigravity",
          base_fallback_provider_ids: ["claude"],
        },
      ],
    };
    const onSave = vi.fn();
    render(<SettingsModal view={overriddenView} onSave={onSave} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "团队配置" }));
    fireEvent.click(screen.getByTestId("agent-reset-wangye"));
    fireEvent.click(screen.getByTestId("settings-save"));
    const patch: HubSettingsPatch = onSave.mock.calls[0][0];
    expect(patch.agents!["wangye"]).toEqual({ clear: true });
  });

  it("keeps team routing inside settings and can route the team to Codex", () => {
    const onSave = vi.fn();
    render(<SettingsModal view={baseView} onSave={onSave} onClose={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "CLI 接入" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "团队配置" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "团队配置" }));
    expect(screen.getByRole("heading", { name: "团队配置" })).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("team-use-codex"));
    fireEvent.click(screen.getByTestId("settings-save"));
    const patch: HubSettingsPatch = onSave.mock.calls[0][0];
    expect(patch.agents!["wangye"]).toEqual({
      default_provider_id: "codex",
      fallback_provider_ids: ["fake"],
    });
  });

  it("shows install, verification, and verified lifecycle actions", () => {
    const onVerifyProvider = vi.fn();
    render(
      <SettingsModal
        view={baseView}
        providerStatus={{
          providers: [
            { id: "codex", label: "Codex CLI", enabled: true, available: true, version: "codex 1.0" },
            { id: "claude", label: "Claude", enabled: false, available: false, error: "command not found: claude" },
            { id: "antigravity", label: "Antigravity", enabled: false, available: true, version: "agy 1.1.5" },
          ],
          loading: false,
          error: null,
          refresh: vi.fn(),
        }}
        onboarding={{
          state: "completed",
          guide_agent_id: "chulan",
          verified_provider_ids: ["codex"],
          providers: [
            { id: "codex", label: "Codex CLI", available: true, verified: true, activated: true },
            {
              id: "claude",
              label: "Claude",
              available: false,
              verified: false,
              activated: false,
              install_command: "curl claude | bash",
              install_url: "https://code.claude.com/docs/en/quickstart",
              auth_command: "claude auth login",
            },
            {
              id: "antigravity",
              label: "Antigravity",
              available: true,
              verified: false,
              activated: false,
              auth_command: "agy",
            },
          ],
          dismissed_real_cli_setup: false,
        }}
        onVerifyProvider={onVerifyProvider}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByTestId("provider-card-codex")).toHaveTextContent("已验证");
    expect(screen.getByTestId("provider-card-claude")).toHaveTextContent("未安装");
    expect(screen.getByTestId("provider-card-antigravity")).toHaveTextContent("已安装 · 待验证");
    fireEvent.click(screen.getByRole("button", { name: "验证真实对话" }));
    expect(onVerifyProvider).toHaveBeenCalledWith("antigravity");
  });
});
