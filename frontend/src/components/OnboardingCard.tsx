import { useState } from "react";
import type { HealthView, OnboardingProviderStatus, OnboardingView } from "../types";

export interface OnboardingCardProps {
  health: HealthView | null;
  healthLoading: boolean;
  healthError: string | null;
  onboarding: OnboardingView | null;
  actionBusy: boolean;
  actionError: string | null;
  onStart: () => void;
  onRetryHealth: () => void;
  onComplete: () => void;
  onSkipRealCli: () => void;
  onUseProviderForTeam: (providerId: string) => void;
  onRefreshProviders: () => void;
  onVerifyProvider: (providerId: string) => void;
  onActivateProvider: (providerId: string) => void;
  onOpenTeamSettings?: () => void;
}

export function OnboardingCard(props: OnboardingCardProps) {
  const [copiedCommand, setCopiedCommand] = useState<string | null>(null);

  function copyCommand(key: string, command?: string | null) {
    if (!command) return;
    void navigator.clipboard?.writeText(command);
    setCopiedCommand(key);
  }

  if (props.healthLoading || (!props.health && !props.healthError)) {
    return (
      <section className="onboarding-card" aria-label="启动引导" aria-live="polite">
        <p className="onboarding-kicker">首次启动</p>
        <h2>正在确认 AgentClub 状态…</h2>
        <p>马上就好，我们在检查聊天室和演示角色。</p>
      </section>
    );
  }

  if (props.healthError || !props.health) {
    return (
      <section className="onboarding-card onboarding-card--error" aria-label="启动引导">
        <p className="onboarding-kicker">还差一步</p>
        <h2>还没有连接到 AgentClub 后端</h2>
        <p>请回到启动项目的终端，查看第一条错误信息，然后重新连接。</p>
        {props.healthError ? <p role="alert" className="onboarding-error">{props.healthError}</p> : null}
        <button type="button" onClick={props.onRetryHealth}>重新连接</button>
      </section>
    );
  }

  const state = props.onboarding?.state ?? "ready_for_first_chat";
  const providers = props.onboarding?.providers ?? [];
  const codex = providers.find((provider) => provider.id === "codex");
  const currentProvider = providers.find(
    (provider) => provider.id === props.onboarding?.verification_provider_id,
  );
  const activatedProviderId = props.onboarding?.activated_provider_id;
  const isExtensionVerification = Boolean(
    activatedProviderId
    && currentProvider
    && currentProvider.id !== activatedProviderId,
  );

  if (state === "first_chat_running") {
    return (
      <section className="onboarding-card" aria-label="启动引导" aria-live="polite">
        <p className="onboarding-kicker">第 2 步，共 4 步</p>
        <h2>张楚岚正在确认聊天室状态…</h2>
        <p>首次对话使用内置演示模式，不会发起真实模型请求。</p>
      </section>
    );
  }

  if (state === "provider_verifying") {
    return (
      <section className="onboarding-card" aria-label="启动引导" aria-live="polite">
        <p className="onboarding-kicker">正在验证真实 CLI</p>
        <h2>等待 {currentProvider?.label || "真实 CLI"} 通过 MCP 回一句话…</h2>
        <p>这次验证只允许所选 CLI，不会由演示模式代答。验证完成前，团队设置不会改变。</p>
        <p className="onboarding-cost-note" role="note">本次验证正在使用你的 CLI 账号发起 1 次模型请求，可能计入套餐额度或产生费用。</p>
      </section>
    );
  }

  if (state === "provider_verified" && currentProvider) {
    if (isExtensionVerification) {
      return (
        <section className="onboarding-card onboarding-card--success" aria-label="启动引导">
          <p className="onboarding-kicker">扩展 CLI 验证成功</p>
          <h2>{currentProvider.label} 已经能够通过 AgentClub 发言</h2>
          <p>原有 Codex 配置没有改变。现在可以在团队配置中，把它分配给张楚岚、冯宝宝或王也，并保留 Codex 作为回退。</p>
          <div className="onboarding-actions">
            <button
              type="button"
              onClick={props.onOpenTeamSettings ?? props.onComplete}
              disabled={props.actionBusy}
            >
              完成并打开团队配置
            </button>
            <button type="button" className="button-secondary" onClick={props.onComplete} disabled={props.actionBusy}>
              稍后配置
            </button>
          </div>
        </section>
      );
    }
    return (
      <section className="onboarding-card onboarding-card--success" aria-label="启动引导">
        <p className="onboarding-kicker">{currentProvider.label} 验证成功</p>
        <h2>Codex 已经完成第一次真实对话</h2>
        <p>现在让它接管张楚岚，并请宝儿姐和王也各自介绍自己。后两位使用演示模式，不会追加真实模型请求。</p>
        <div className="onboarding-actions">
          <button type="button" onClick={() => props.onActivateProvider(currentProvider.id)} disabled={props.actionBusy}>
            {props.actionBusy ? "正在召集团队…" : "让 Codex 接管并认识团队"}
          </button>
          <button type="button" className="button-secondary" onClick={props.onSkipRealCli} disabled={props.actionBusy}>
            暂不接管
          </button>
        </div>
        {props.actionError ? <p role="alert" className="onboarding-error">{props.actionError}</p> : null}
      </section>
    );
  }

  if (state === "team_intro_running") {
    return (
      <section className="onboarding-card" aria-label="启动引导" aria-live="polite">
        <p className="onboarding-kicker">最后一步</p>
        <h2>宝儿姐和王也正在进场…</h2>
        <p>他们会各用一句话介绍自己的性格；这里使用演示模式，不会追加真实模型请求。</p>
      </section>
    );
  }

  if (state === "team_intro_failed") {
    const providerId = activatedProviderId || props.onboarding?.verification_provider_id;
    return (
      <section className="onboarding-card onboarding-card--error" aria-label="启动引导">
        <p className="onboarding-kicker">团队介绍未完成</p>
        <h2>宝儿姐或王也刚才没有成功发言</h2>
        <p>真实 CLI 接管已经保存，可以重新运行这段演示介绍。</p>
        {providerId ? (
          <button type="button" onClick={() => props.onActivateProvider(providerId)} disabled={props.actionBusy}>
            {props.actionBusy ? "正在重试…" : "重新介绍团队"}
          </button>
        ) : null}
      </section>
    );
  }

  if (state === "team_intro_completed") {
    const providerLabel = providers.find(
      (provider) => provider.id === activatedProviderId,
    )?.label || "Codex CLI";
    const expansionProviders = providers.filter(
      (provider) => provider.id === "claude" || provider.id === "antigravity",
    );
    return (
      <section className="onboarding-card onboarding-card--success onboarding-card--wide" aria-label="启动引导">
        <p className="onboarding-kicker">Codex-first 已完成</p>
        <h2>三位角色都到齐了</h2>
        <ul className="onboarding-checks">
          <li>张楚岚 · 通透机敏 · {providerLabel}</li>
          <li>冯宝宝 · 坦率好奇 · 演示模式</li>
          <li>王也 · 松弛笃定 · 演示模式</li>
        </ul>

        <div className="onboarding-expansion">
          <div className="onboarding-expansion-heading">
            <strong>可选扩展</strong>
            <span>Codex 会继续作为可靠回退</span>
          </div>
          <div className="onboarding-provider-list">
            {expansionProviders.map((provider) => (
              <ProviderSetupCard
                key={provider.id}
                provider={provider}
                busy={props.actionBusy}
                copiedCommand={copiedCommand}
                onCopy={copyCommand}
                onVerify={props.onVerifyProvider}
                compact
              />
            ))}
          </div>
        </div>

        <div className="onboarding-actions">
          <button
            type="button"
            onClick={() => props.onUseProviderForTeam(activatedProviderId || "codex")}
            disabled={props.actionBusy}
          >
            {props.actionBusy ? "正在配置…" : "一键让三位角色使用 Codex"}
          </button>
          <button
            type="button"
            className="button-secondary"
            onClick={props.onOpenTeamSettings ?? props.onComplete}
            disabled={props.actionBusy}
          >
            打开团队配置
          </button>
          <button type="button" className="button-secondary" onClick={props.onComplete} disabled={props.actionBusy}>
            保持当前配置
          </button>
        </div>
        <p className="onboarding-cost-note" role="note">每次真实 CLI 验证都会使用对应账号发起 1 次模型请求，可能计入套餐额度或产生费用；安装、登录和页面检测本身不会调用模型。</p>
      </section>
    );
  }

  if (state === "first_chat_succeeded" || state === "provider_verification_failed") {
    const failed = state === "provider_verification_failed";
    const targetProvider = failed ? currentProvider ?? codex : codex;
    return (
      <section className={`onboarding-card${failed ? " onboarding-card--error" : " onboarding-card--success"}`} aria-label="启动引导">
        <p className="onboarding-kicker">{failed ? "真实 CLI 验证未通过" : "首次对话成功"}</p>
        <h2>{failed ? "没有切换，演示模式仍然可用" : "下一步，只接入 Codex"}</h2>
        {failed ? (
          <p>正式设置没有变化。检查 CLI 的安装和登录状态后可以重试。</p>
        ) : (
          <p>先让 Codex 完成第一轮真实对话；成功后再选择是否安装 Claude Code 和 Antigravity CLI。</p>
        )}

        {failed && props.onboarding?.verification_error ? (
          <p role="alert" className="onboarding-error">{props.onboarding.verification_error}</p>
        ) : null}

        {targetProvider ? (
          <div className="onboarding-provider-list onboarding-provider-list--primary">
            <ProviderSetupCard
              provider={targetProvider}
              recommended
              busy={props.actionBusy}
              copiedCommand={copiedCommand}
              onCopy={copyCommand}
              onVerify={props.onVerifyProvider}
            />
          </div>
        ) : (
          <p className="onboarding-error">当前配置中没有找到 Codex CLI，请检查 Provider 配置。</p>
        )}

        <p className="onboarding-cost-note" role="note">验证会使用你的 Codex 账号发起 1 次真实模型请求，可能计入套餐额度或产生费用；安装、登录和页面检测本身不会调用模型。</p>
        <div className="onboarding-actions">
          <button type="button" className="button-secondary" onClick={props.onRefreshProviders} disabled={props.actionBusy}>
            重新检测
          </button>
          <button type="button" className="button-secondary" onClick={props.onSkipRealCli} disabled={props.actionBusy}>
            先用演示模式
          </button>
        </div>
        {props.actionError ? <p role="alert" className="onboarding-error">{props.actionError}</p> : null}
      </section>
    );
  }

  const isRetry = state === "first_chat_failed";
  return (
    <section className={`onboarding-card${isRetry ? " onboarding-card--error" : ""}`} aria-label="启动引导">
      <p className="onboarding-kicker">第 1 步已完成</p>
      <h2>{isRetry ? "张楚岚刚才没有成功回复" : "AgentClub 已启动"}</h2>
      {isRetry ? (
        <p>聊天室仍然可用。重新运行一次演示对话即可，不需要安装真实 CLI。</p>
      ) : (
        <ul className="onboarding-checks">
          <li>后端服务已连接</li>
          <li>3 个角色已加载</li>
          <li>演示 Provider 可用</li>
        </ul>
      )}
      <button type="button" onClick={props.onStart} disabled={props.actionBusy}>
        {props.actionBusy ? "正在叫醒张楚岚…" : isRetry ? "重试首次对话" : "和张楚岚打个招呼"}
      </button>
      {props.actionError ? <p role="alert" className="onboarding-error">{props.actionError}</p> : null}
    </section>
  );
}

function ProviderSetupCard({
  provider,
  recommended = false,
  compact = false,
  busy,
  copiedCommand,
  onCopy,
  onVerify,
}: {
  provider: OnboardingProviderStatus;
  recommended?: boolean;
  compact?: boolean;
  busy: boolean;
  copiedCommand: string | null;
  onCopy: (key: string, command?: string | null) => void;
  onVerify: (providerId: string) => void;
}) {
  const installKey = `${provider.id}:install`;
  const authKey = `${provider.id}:auth`;
  const status = provider.verified
    ? "已验证，可以分配给角色"
    : provider.available
      ? provider.version || "已安装，等待登录验证"
      : "未安装";

  return (
    <article className={`onboarding-provider-option${recommended ? " is-recommended" : ""}${compact ? " is-compact" : ""}`}>
      <div className="onboarding-provider-option-heading">
        <strong>{provider.label}</strong>
        {recommended ? <span>推荐第一步</span> : null}
      </div>
      <p>{status}</p>
      {!provider.available ? (
        <div className="onboarding-install-actions">
          {provider.install_command ? (
            <button
              type="button"
              className="button-secondary"
              onClick={() => onCopy(installKey, provider.install_command)}
            >
              {copiedCommand === installKey ? "安装命令已复制" : "复制安装命令"}
            </button>
          ) : null}
          {provider.install_url ? (
            <a href={provider.install_url} target="_blank" rel="noreferrer">官方安装说明</a>
          ) : null}
        </div>
      ) : !provider.verified ? (
        <>
          {provider.auth_command ? (
            <button
              type="button"
              className="onboarding-command"
              onClick={() => onCopy(authKey, provider.auth_command)}
            >
              {copiedCommand === authKey ? "登录命令已复制" : `登录：${provider.auth_command}`}
            </button>
          ) : null}
          <button type="button" onClick={() => onVerify(provider.id)} disabled={busy}>
            {busy ? "正在启动验证…" : `我已登录，验证 ${provider.label}`}
          </button>
        </>
      ) : null}
    </article>
  );
}
