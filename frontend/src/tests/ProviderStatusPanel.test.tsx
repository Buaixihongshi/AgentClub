import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProviderStatusPanel } from "../components/ProviderStatusPanel";

describe("ProviderStatusPanel", () => {
  it("shows real supported providers and hides internal or retired ones", () => {
    render(
      <ProviderStatusPanel
        providers={[
          { id: "fake", label: "Fake CLI", enabled: true, available: true },
          { id: "gemini", label: "Gemini CLI", enabled: false, available: true },
          { id: "codex", label: "Codex CLI", enabled: false, available: true },
          { id: "antigravity", label: "Antigravity CLI", enabled: false, available: true },
        ]}
        loading={false}
        error={null}
        refresh={vi.fn()}
      />,
    );

    expect(screen.queryByText("Fake CLI")).not.toBeInTheDocument();
    expect(screen.queryByText("Gemini CLI")).not.toBeInTheDocument();
    expect(screen.getByText("Codex CLI")).toBeInTheDocument();
    expect(screen.getByText("Antigravity CLI")).toBeInTheDocument();
  });

  it("distinguishes installed providers from MCP-verified providers", () => {
    render(
      <ProviderStatusPanel
        providers={[
          { id: "codex", label: "Codex CLI", enabled: true, available: true },
          { id: "claude", label: "Claude Code", enabled: true, available: true },
        ]}
        loading={false}
        error={null}
        refresh={vi.fn()}
        onboarding={{
          state: "completed",
          guide_agent_id: "chulan",
          verified_provider_ids: ["codex"],
          providers: [
            {
              id: "codex",
              label: "Codex CLI",
              available: true,
              verified: true,
              activated: true,
            },
            {
              id: "claude",
              label: "Claude Code",
              available: true,
              verified: false,
              activated: false,
            },
          ],
          dismissed_real_cli_setup: false,
        }}
      />,
    );

    expect(screen.getByText("已验证")).toBeInTheDocument();
    expect(screen.getByText("已安装 · 待验证")).toBeInTheDocument();
  });
});
