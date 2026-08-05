import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OnboardingCard, type OnboardingCardProps } from "../components/OnboardingCard";

const health = {
  status: "ok" as const,
  version: "0.1.0",
  database: "ready" as const,
  config: "ready" as const,
  fake_provider: "ready" as const,
};

function props(overrides: Partial<OnboardingCardProps> = {}): OnboardingCardProps {
  return {
    health,
    healthLoading: false,
    healthError: null,
    onboarding: {
      state: "ready_for_first_chat",
      guide_agent_id: "chulan",
      conversation_id: null,
      mission_id: null,
      first_reply_completed_at: null,
      completed_at: null,
      verified_provider_ids: [],
      providers: [],
      dismissed_real_cli_setup: false,
    },
    actionBusy: false,
    actionError: null,
    onStart: vi.fn(),
    onRetryHealth: vi.fn(),
    onComplete: vi.fn(),
    onSkipRealCli: vi.fn(),
    onUseProviderForTeam: vi.fn(),
    onRefreshProviders: vi.fn(),
    onVerifyProvider: vi.fn(),
    onActivateProvider: vi.fn(),
    ...overrides,
  };
}

describe("OnboardingCard", () => {
  it("shows the ready checklist and starts the first chat", async () => {
    const onStart = vi.fn();
    render(<OnboardingCard {...props({ onStart })} />);

    expect(screen.getByText("后端服务已连接")).toBeInTheDocument();
    expect(screen.getByText("3 个角色已加载")).toBeInTheDocument();
    expect(screen.getByText("演示 Provider 可用")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "和张楚岚打个招呼" }));

    expect(onStart).toHaveBeenCalledOnce();
  });

  it("keeps the first real setup focused on Codex", async () => {
    const onVerifyProvider = vi.fn();
    const onComplete = vi.fn();
    const base = props();
    render(
      <OnboardingCard
        {...base}
        onboarding={{
          ...base.onboarding!,
          state: "first_chat_succeeded",
          recommended_provider_id: "codex",
          providers: [{
            id: "codex",
            label: "Codex CLI",
            available: true,
            version: "Codex CLI 9.9.9",
            verified: false,
            activated: false,
          }],
        }}
        onVerifyProvider={onVerifyProvider}
        onComplete={onComplete}
      />,
    );

    expect(screen.getByText("下一步，只接入 Codex")).toBeInTheDocument();
    expect(screen.getByText("Codex CLI 9.9.9")).toBeInTheDocument();
    expect(screen.getByText("推荐第一步")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("可能计入套餐额度或产生费用");
    expect(screen.queryByText("Claude Code")).not.toBeInTheDocument();
    expect(screen.queryByText("Antigravity CLI")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "我已登录，验证 Codex CLI" }));

    expect(onVerifyProvider).toHaveBeenCalledWith("codex");
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("only activates the selected provider after a successful verification", async () => {
    const onActivateProvider = vi.fn();
    const base = props();
    render(
      <OnboardingCard
        {...base}
        onboarding={{
          ...base.onboarding!,
          state: "provider_verified",
          verification_provider_id: "codex",
          verified_provider_ids: ["codex"],
          providers: [{
            id: "codex",
            label: "Codex CLI",
            available: true,
            verified: true,
            activated: false,
          }],
        }}
        onActivateProvider={onActivateProvider}
      />,
    );

    expect(screen.getByText("Codex 已经完成第一次真实对话")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "让 Codex 接管并认识团队" }));

    expect(onActivateProvider).toHaveBeenCalledWith("codex");
  });

  it("keeps onboarding open until baobao and wangye have introduced themselves", async () => {
    const onComplete = vi.fn();
    const onUseProviderForTeam = vi.fn();
    const onOpenTeamSettings = vi.fn();
    const onVerifyProvider = vi.fn();
    const base = props();
    const { rerender } = render(
      <OnboardingCard
        {...base}
        onboarding={{
          ...base.onboarding!,
          state: "team_intro_running",
          activated_provider_id: "codex",
          team_intro_mission_id: "m_intro",
        }}
        onComplete={onComplete}
        onUseProviderForTeam={onUseProviderForTeam}
        onOpenTeamSettings={onOpenTeamSettings}
        onVerifyProvider={onVerifyProvider}
      />,
    );

    expect(screen.getByText("宝儿姐和王也正在进场…")).toBeInTheDocument();
    expect(screen.getByText(/不会追加真实模型请求/)).toBeInTheDocument();

    rerender(
      <OnboardingCard
        {...base}
        onboarding={{
          ...base.onboarding!,
          state: "team_intro_completed",
          activated_provider_id: "codex",
          team_intro_mission_id: "m_intro",
          team_intro_completed_at: "2026-07-30T00:00:00Z",
          providers: [{
            id: "codex",
            label: "Codex CLI",
            available: true,
            verified: true,
            activated: true,
          }, {
            id: "claude",
            label: "Claude Code",
            available: false,
            verified: false,
            activated: false,
            install_command: "curl claude | bash",
            install_url: "https://code.claude.com/docs/en/quickstart",
            auth_command: "claude auth login",
          }, {
            id: "antigravity",
            label: "Antigravity CLI",
            available: true,
            version: "1.1.5",
            verified: false,
            activated: false,
            install_command: "curl agy | bash",
            install_url: "https://antigravity.google/docs/cli/install",
            auth_command: "agy",
          }],
        }}
        onComplete={onComplete}
        onUseProviderForTeam={onUseProviderForTeam}
        onOpenTeamSettings={onOpenTeamSettings}
        onVerifyProvider={onVerifyProvider}
      />,
    );

    expect(screen.getByText("三位角色都到齐了")).toBeInTheDocument();
    expect(screen.getByText(/冯宝宝 · 执行与产物/)).toBeInTheDocument();
    expect(screen.getByText(/王也 · 分析与复盘/)).toBeInTheDocument();
    expect(screen.getByText("可选扩展")).toBeInTheDocument();
    expect(screen.getByText("Claude Code")).toBeInTheDocument();
    expect(screen.getByText("Antigravity CLI")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("每次真实 CLI 验证都会使用对应账号");
    expect(screen.getByRole("link", { name: "官方安装说明" })).toHaveAttribute(
      "href",
      "https://code.claude.com/docs/en/quickstart",
    );
    await userEvent.click(screen.getByRole("button", { name: "我已登录，验证 Antigravity CLI" }));
    expect(onVerifyProvider).toHaveBeenCalledWith("antigravity");

    await userEvent.click(screen.getByRole("button", { name: "一键让三位角色使用 Codex" }));
    expect(onUseProviderForTeam).toHaveBeenCalledWith("codex");
    expect(onComplete).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "打开团队配置" }));
    expect(onOpenTeamSettings).toHaveBeenCalledOnce();

    await userEvent.click(screen.getByRole("button", { name: "保持当前分工" }));
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it("returns an additionally verified CLI to persistent team settings", async () => {
    const onOpenTeamSettings = vi.fn();
    const base = props();
    render(
      <OnboardingCard
        {...base}
        onboarding={{
          ...base.onboarding!,
          state: "provider_verified",
          activated_provider_id: "codex",
          verification_provider_id: "claude",
          verified_provider_ids: ["codex", "claude"],
          providers: [{
            id: "claude",
            label: "Claude Code",
            available: true,
            verified: true,
            activated: false,
          }],
        }}
        onOpenTeamSettings={onOpenTeamSettings}
      />,
    );

    expect(screen.getByText("Claude Code 已经能够通过 YiRen Hub 发言")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "完成并打开团队配置" }));
    expect(onOpenTeamSettings).toHaveBeenCalledOnce();
  });

  it("offers a retry when the backend is unavailable", async () => {
    const onRetryHealth = vi.fn();
    render(
      <OnboardingCard
        {...props({
          health: null,
          healthError: "YiRen Hub 后端尚未就绪",
          onRetryHealth,
        })}
      />,
    );

    expect(screen.getByText("还没有连接到 YiRen Hub 后端")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新连接" }));

    expect(onRetryHealth).toHaveBeenCalledOnce();
  });
});
