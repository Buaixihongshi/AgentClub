import { useState, useMemo } from "react";
import { Activity, Cable, SlidersHorizontal, UsersRound, X } from "lucide-react";
import type { ProviderStatusState } from "../hooks/useProviderStatus";
import { ObservabilityOverview } from "./ObservabilityOverview";
import type {
  AgentOverridePatch,
  HubSettingsAgentView,
  HubSettingsView,
  HubSettingsPatch,
  HubSettingsProviderView,
  OnboardingProviderStatus,
  OnboardingView,
} from "../types";

const INTERNAL_PROVIDERS = new Set(["fake"]);
const RETIRED_PROVIDERS = new Set(["gemini"]);

const RUNTIME_LABELS: Record<string, string> = {
  use_fake_cli: "使用演示模式",
  max_turns: "最大 Turn 数",
  max_a2a_invocations: "最大 A2A 调用数",
  cli_timeout: "CLI 超时（秒）",
  a2a_idle_timeout: "A2A 无进展超时（秒，0=关闭）",
};

interface AgentDraft {
  defaultProvider?: string;
  fallbacks?: string[];
  clear?: boolean;
}

type SettingsSectionId = "providers" | "team" | "runtime" | "observability";

interface Props {
  view: HubSettingsView;
  providerStatus?: ProviderStatusState;
  onboarding?: OnboardingView | null;
  onRefreshProviders?: () => void;
  onVerifyProvider?: (providerId: string) => void;
  onSave: (patch: HubSettingsPatch) => void;
  onClose: () => void;
}

export function SettingsModal({
  view,
  providerStatus,
  onboarding,
  onRefreshProviders,
  onVerifyProvider,
  onSave,
  onClose,
}: Props) {
  const [providerDraft, setProviderDraft] = useState<Record<string, boolean>>({});
  const [runtimeDraft, setRuntimeDraft] = useState<Record<string, string>>({});
  const [agentDraft, setAgentDraft] = useState<Record<string, AgentDraft>>({});
  const [copiedCommand, setCopiedCommand] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<SettingsSectionId>("providers");

  const enabledProviders = useMemo(() => {
    const map: Record<string, boolean> = {};
    for (const p of view.providers) {
      map[p.id] = p.id in providerDraft ? providerDraft[p.id] : p.enabled.value;
    }
    return map;
  }, [view.providers, providerDraft]);

  const agentRows = view.agents ?? [];
  const visibleProviders = view.providers.filter(
    (provider) => !INTERNAL_PROVIDERS.has(provider.id) && !RETIRED_PROVIDERS.has(provider.id),
  );
  const routeProviders = view.providers.filter(
    (provider) => !RETIRED_PROVIDERS.has(provider.id),
  );
  const diagnosticsById = useMemo(
    () => new Map((providerStatus?.providers ?? []).map((provider) => [provider.id, provider])),
    [providerStatus?.providers],
  );
  const onboardingProvidersById = useMemo(
    () => new Map((onboarding?.providers ?? []).map((provider) => [provider.id, provider])),
    [onboarding?.providers],
  );
  const verifiedProviderIds = useMemo(
    () => new Set(onboarding?.verified_provider_ids ?? []),
    [onboarding?.verified_provider_ids],
  );
  const providerDisplayName = (provider: HubSettingsProviderView) =>
    provider.id === "fake" ? "演示模式（内置）" : provider.label;
  const canRouteToProvider = (providerId: string) => (
    providerId === "fake"
    || verifiedProviderIds.has(providerId)
    || Boolean(enabledProviders[providerId])
  );

  function copyCommand(key: string, command?: string | null) {
    if (!command) return;
    void navigator.clipboard?.writeText(command);
    setCopiedCommand(key);
  }

  function lifecycleFor(provider: HubSettingsProviderView) {
    const diagnostic = diagnosticsById.get(provider.id);
    const setup = onboardingProvidersById.get(provider.id);
    const verified = verifiedProviderIds.has(provider.id) || Boolean(setup?.verified);
    if (verified) return { label: "已验证", tone: "ready", diagnostic, setup };
    if (!providerStatus) {
      return { label: "状态未检测", tone: "checking", diagnostic, setup };
    }
    if (providerStatus?.loading && !diagnostic) {
      return { label: "检测中", tone: "checking", diagnostic, setup };
    }
    if (diagnostic?.available) {
      return { label: "已安装 · 待验证", tone: "installed", diagnostic, setup };
    }
    return { label: "未安装", tone: "missing", diagnostic, setup };
  }

  function effectiveAgent(agent: HubSettingsAgentView): {
    defaultProvider: string;
    fallbacks: string[];
    isUserOverride: boolean;
  } {
    const draft = agentDraft[agent.id];
    if (draft?.clear) {
      return {
        defaultProvider: agent.base_default_provider_id,
        fallbacks: agent.base_fallback_provider_ids,
        isUserOverride: false,
      };
    }
    const defaultProvider = draft?.defaultProvider ?? agent.default_provider_id.value;
    const fallbacks = draft?.fallbacks ?? agent.fallback_provider_ids.value;
    const isUserOverride =
      draft !== undefined ||
      agent.default_provider_id.source === "user" ||
      agent.fallback_provider_ids.source === "user";
    return { defaultProvider, fallbacks, isUserOverride };
  }

  function agentHasDiff(agent: HubSettingsAgentView): boolean {
    const draft = agentDraft[agent.id];
    if (!draft) return false;
    if (draft.clear) {
      return (
        agent.default_provider_id.source === "user" ||
        agent.fallback_provider_ids.source === "user"
      );
    }
    if (draft.defaultProvider !== undefined &&
        draft.defaultProvider !== agent.default_provider_id.value) return true;
    if (draft.fallbacks !== undefined &&
        !sameStringList(draft.fallbacks, agent.fallback_provider_ids.value)) return true;
    return false;
  }

  const hasDiff = useMemo(() => {
    for (const p of view.providers) {
      if (p.id in providerDraft && providerDraft[p.id] !== p.enabled.value) return true;
    }
    for (const [key, raw] of Object.entries(runtimeDraft)) {
      const rtMap = view.runtime as unknown as Record<string, { value: unknown }>;
      const original = rtMap[key];
      if (!original) continue;
      if (typeof original.value === "boolean") {
        const newVal = raw === "true";
        if (newVal !== original.value) return true;
      } else {
        const numVal = Number(raw);
        if (!isNaN(numVal) && numVal !== original.value) return true;
      }
    }
    for (const agent of agentRows) {
      if (agentHasDiff(agent)) return true;
    }
    return false;
  }, [view, providerDraft, runtimeDraft, agentDraft, agentRows]);

  function buildPatch(): HubSettingsPatch {
    const patch: HubSettingsPatch = {};
    const providerPatches: Record<string, { enabled: boolean }> = {};
    for (const p of view.providers) {
      if (p.id in providerDraft && providerDraft[p.id] !== p.enabled.value) {
        providerPatches[p.id] = { enabled: providerDraft[p.id] };
      }
    }
    if (Object.keys(providerPatches).length > 0) patch.providers = providerPatches;

    const rt: Record<string, unknown> = {};
    for (const [key, raw] of Object.entries(runtimeDraft)) {
      const rtMap = view.runtime as unknown as Record<string, { value: unknown }>;
      const original = rtMap[key];
      if (!original) continue;
      if (typeof original.value === "boolean") {
        const newVal = raw === "true";
        if (newVal !== original.value) rt[key] = newVal;
      } else {
        const numVal = Number(raw);
        if (!isNaN(numVal) && numVal !== original.value) rt[key] = numVal;
      }
    }
    if (Object.keys(rt).length > 0) patch.runtime = rt;

    const agentPatches: Record<string, AgentOverridePatch> = {};
    for (const agent of agentRows) {
      const draft = agentDraft[agent.id];
      if (!draft) continue;
      if (draft.clear) {
        agentPatches[agent.id] = { clear: true };
        continue;
      }
      const entry: AgentOverridePatch = {};
      if (draft.defaultProvider !== undefined &&
          draft.defaultProvider !== agent.default_provider_id.value) {
        entry.default_provider_id = draft.defaultProvider;
      }
      if (draft.fallbacks !== undefined &&
          !sameStringList(draft.fallbacks, agent.fallback_provider_ids.value)) {
        entry.fallback_provider_ids = draft.fallbacks;
      }
      if (Object.keys(entry).length > 0) agentPatches[agent.id] = entry;
    }
    if (Object.keys(agentPatches).length > 0) patch.agents = agentPatches;
    return patch;
  }

  function handleSave() {
    onSave(buildPatch());
  }

  function toggleProvider(id: string) {
    setProviderDraft((prev) => ({ ...prev, [id]: !enabledProviders[id] }));
  }

  function setAgentDefault(agentId: string, providerId: string) {
    setAgentDraft((prev) => ({
      ...prev,
      [agentId]: { ...(prev[agentId] ?? {}), defaultProvider: providerId, clear: false },
    }));
  }

  function toggleAgentFallback(agent: HubSettingsAgentView, providerId: string) {
    setAgentDraft((prev) => {
      const eff = prev[agent.id]?.fallbacks ?? agent.fallback_provider_ids.value;
      const next = eff.includes(providerId)
        ? eff.filter((p) => p !== providerId)
        : [...eff, providerId];
      return {
        ...prev,
        [agent.id]: { ...(prev[agent.id] ?? {}), fallbacks: next, clear: false },
      };
    });
  }

  function resetAgent(agentId: string) {
    setAgentDraft((prev) => ({ ...prev, [agentId]: { clear: true } }));
  }

  function routeAllAgentsTo(providerId: string) {
    const next: Record<string, AgentDraft> = {};
    for (const agent of agentRows) {
      next[agent.id] = {
        defaultProvider: providerId,
        fallbacks: providerId === "fake" ? [] : ["fake"],
        clear: false,
      };
    }
    setAgentDraft(next);
  }

  const sourceLabel = (s: string) => {
    switch (s) {
      case "user": return "用户";
      case "env": return "env";
      case "default": return "默认";
      case "committed": return "committed";
      default: return s;
    }
  };

  const warningProviders = visibleProviders.filter(
    (p) => !INTERNAL_PROVIDERS.has(p.id) && enabledProviders[p.id],
  );

  return (
    <div className="settings-modal-backdrop" onClick={onClose}>
      <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-modal-header">
          <div>
            <h2>设置</h2>
            <p>管理 CLI、角色配置、运行参数与全局观测</p>
          </div>
          <button
            type="button"
            className="settings-close"
            data-testid="settings-close"
            aria-label="关闭设置"
            onClick={onClose}
          >
            <X size={19} aria-hidden="true" />
          </button>
        </div>

        <div className="settings-modal-content">
          <nav className="settings-nav" aria-label="设置分类">
            <button
              type="button"
              className={activeSection === "providers" ? "is-active" : ""}
              aria-pressed={activeSection === "providers"}
              onClick={() => setActiveSection("providers")}
            >
              <Cable size={17} aria-hidden="true" />
              <span>CLI 接入</span>
            </button>
            <button
              type="button"
              className={activeSection === "team" ? "is-active" : ""}
              aria-pressed={activeSection === "team"}
              onClick={() => setActiveSection("team")}
            >
              <UsersRound size={17} aria-hidden="true" />
              <span>团队配置</span>
            </button>
            <button
              type="button"
              className={activeSection === "runtime" ? "is-active" : ""}
              aria-pressed={activeSection === "runtime"}
              onClick={() => setActiveSection("runtime")}
            >
              <SlidersHorizontal size={17} aria-hidden="true" />
              <span>运行参数</span>
            </button>
            <button
              type="button"
              className={activeSection === "observability" ? "is-active" : ""}
              aria-pressed={activeSection === "observability"}
              onClick={() => setActiveSection("observability")}
            >
              <Activity size={17} aria-hidden="true" />
              <span>观测</span>
            </button>
          </nav>

          <div className="settings-panel">
            <div className="settings-modal-body">
          {activeSection === "providers" ? (
          <section className="settings-section" aria-labelledby="settings-providers-title">
            <div className="settings-section-heading">
              <div>
                <h3 id="settings-providers-title">CLI 接入</h3>
                <p className="settings-section-hint">先安装和验证 CLI，再启用并分配给角色。</p>
              </div>
              <button
                type="button"
                className="settings-refresh-btn"
                onClick={onRefreshProviders ?? providerStatus?.refresh}
                disabled={providerStatus?.loading}
              >
                {providerStatus?.loading ? "检测中…" : "重新检测"}
              </button>
            </div>
            <div className="settings-provider-grid">
              {visibleProviders.map((p: HubSettingsProviderView) => {
                const lifecycle = lifecycleFor(p);
                const setup = lifecycle.setup as OnboardingProviderStatus | undefined;
                const diagnostic = lifecycle.diagnostic;
                const verified = lifecycle.tone === "ready";
                const available = Boolean(diagnostic?.available);
                return (
                  <article className="settings-provider-card" key={p.id} data-testid={`provider-card-${p.id}`}>
                    <div className="settings-provider-card-heading">
                      <div>
                        <strong>{p.label}</strong>
                        <span className={`settings-provider-lifecycle is-${lifecycle.tone}`}>{lifecycle.label}</span>
                      </div>
                      <div className="settings-provider-card-controls">
                        <span className="settings-source-badge">
                          {p.id in providerDraft && providerDraft[p.id] !== p.enabled.value
                            ? sourceLabel("user")
                            : sourceLabel(p.enabled.source)}
                        </span>
                        <button
                          type="button"
                          role="switch"
                          aria-label={`启用 ${p.label}`}
                          aria-checked={enabledProviders[p.id]}
                          className={"settings-toggle" + (enabledProviders[p.id] ? " on" : "")}
                          data-testid={"toggle-" + p.id}
                          disabled={Boolean(providerStatus) && !available && !verified && !enabledProviders[p.id]}
                          onClick={() => toggleProvider(p.id)}
                        >
                          {enabledProviders[p.id] ? "ON" : "OFF"}
                        </button>
                      </div>
                    </div>
                    <p className="settings-provider-detail">
                      {diagnostic?.version || diagnostic?.error || (verified ? "已通过 MCP 真实对话验证" : "等待本机检测")}
                    </p>
                    <div className="settings-provider-actions">
                      {!available && !verified ? (
                        <>
                          {setup?.install_command ? (
                            <button type="button" onClick={() => copyCommand(`${p.id}:install`, setup.install_command)}>
                              {copiedCommand === `${p.id}:install` ? "安装命令已复制" : "复制安装命令"}
                            </button>
                          ) : null}
                          {setup?.install_url ? <a href={setup.install_url} target="_blank" rel="noreferrer">官方说明</a> : null}
                        </>
                      ) : !verified ? (
                        <>
                          {setup?.auth_command ? (
                            <button type="button" onClick={() => copyCommand(`${p.id}:auth`, setup.auth_command)}>
                              {copiedCommand === `${p.id}:auth` ? "登录命令已复制" : `登录：${setup.auth_command}`}
                            </button>
                          ) : null}
                          {onVerifyProvider ? (
                            <button type="button" className="settings-provider-verify" onClick={() => onVerifyProvider(p.id)}>
                              验证真实对话
                            </button>
                          ) : null}
                        </>
                      ) : (
                        <span>可以在下方团队配置中分配</span>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
            {warningProviders.length > 0 && (
              <p className="settings-cost-warning">
                启用真实 provider 将向 {warningProviders.map((p) => p.label).join("、")} 发送真实请求并产生费用
              </p>
            )}
          </section>
          ) : null}

          {activeSection === "team" ? (
          <section className="settings-section" aria-labelledby="settings-team-title">
              <h3 id="settings-team-title">团队配置</h3>
              <p className="settings-section-hint">
                每个角色可以复用已经启用的 CLI，并保持独立人格和会话上下文。保存后下一个任务生效。
              </p>
              <div className="team-settings-quick-action">
                <div>
                  <strong>快速配置</strong>
                  <p>让三位角色共用 Codex，并在不可用时回退到演示模式。</p>
                </div>
                <button
                  type="button"
                  className="button-secondary"
                  data-testid="team-use-codex"
                  disabled={!enabledProviders.codex || !canRouteToProvider("codex") || agentRows.length === 0}
                  onClick={() => routeAllAgentsTo("codex")}
                >
                  {enabledProviders.codex ? "三位角色使用 Codex" : "Codex 尚未启用"}
                </button>
              </div>
              {agentRows.length === 0 ? (
                <p className="settings-section-hint">暂无可配置角色。</p>
              ) : null}
              {agentRows.map((agent) => {
                const { defaultProvider, fallbacks, isUserOverride } = effectiveAgent(agent);
                return (
                  <div className="settings-agent-row" key={agent.id} data-testid={"agent-row-" + agent.id}>
                    <div className="settings-agent-header">
                      <span className="settings-agent-name">{agent.display_name}</span>
                      <span className="settings-source-badge">
                        {sourceLabel(isUserOverride ? "user" : "committed")}
                      </span>
                      {isUserOverride && (
                        <button
                          type="button"
                          className="settings-agent-reset"
                          data-testid={"agent-reset-" + agent.id}
                          onClick={() => resetAgent(agent.id)}
                        >
                          重置
                        </button>
                      )}
                    </div>
                    <div className="settings-agent-controls">
                      <label className="settings-agent-default">
                        <span>默认</span>
                        <select
                          data-testid={"agent-default-" + agent.id}
                          value={defaultProvider}
                          onChange={(e) => setAgentDefault(agent.id, e.target.value)}
                        >
                          {routeProviders.map((p) => (
                            <option key={p.id} value={p.id} disabled={!canRouteToProvider(p.id)}>
                              {providerDisplayName(p)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <div className="settings-agent-fallback">
                        <span>Fallback</span>
                        <div className="settings-agent-fallback-options">
                          {routeProviders
                            .filter((p) => p.id !== defaultProvider)
                            .map((p) => (
                              <label key={p.id} className="settings-agent-fallback-option">
                                <input
                                  type="checkbox"
                                  data-testid={"agent-fallback-" + agent.id + "-" + p.id}
                                  checked={fallbacks.includes(p.id)}
                                  disabled={!canRouteToProvider(p.id)}
                                  onChange={() => toggleAgentFallback(agent, p.id)}
                                />
                                <span>{providerDisplayName(p)}</span>
                              </label>
                            ))}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
          </section>
          ) : null}

          {activeSection === "runtime" ? (
          <section className="settings-section" aria-labelledby="settings-runtime-title">
            <h3 id="settings-runtime-title">运行参数</h3>
            <p className="settings-section-hint">控制任务轮次、Agent 接力预算与 CLI 超时。保存后对新任务生效。</p>
            {(Object.keys(RUNTIME_LABELS) as (keyof typeof RUNTIME_LABELS)[]).map((key) => {
              const rtMap = view.runtime as unknown as Record<string, { value: unknown; source: string }>;
              const field = rtMap[key];
              if (!field) return null;
              const isBool = typeof field.value === "boolean";
              const currentValue = key in runtimeDraft ? runtimeDraft[key] : String(field.value);
              const currentSource =
                key in runtimeDraft &&
                (isBool
                  ? (runtimeDraft[key] === "true") !== field.value
                  : Number(runtimeDraft[key]) !== field.value)
                  ? "user"
                  : field.source;

              return (
                <div className="settings-runtime-row" key={key}>
                  <label htmlFor={"rt-" + key}>{RUNTIME_LABELS[key]}</label>
                  {isBool ? (
                    <button
                      type="button"
                      role="switch"
                      aria-checked={currentValue === "true"}
                      className={"settings-toggle" + (currentValue === "true" ? " on" : "")}
                      data-testid={"input-" + key}
                      onClick={() =>
                        setRuntimeDraft((prev) => ({
                          ...prev,
                          [key]: prev[key] !== undefined
                            ? (prev[key] === "true" ? "false" : "true")
                            : (field.value ? "false" : "true"),
                        }))
                      }
                    >
                      {currentValue === "true" ? "ON" : "OFF"}
                    </button>
                  ) : (
                    <input
                      id={"rt-" + key}
                          type="number"
                          min={key === "a2a_idle_timeout" ? 0 : undefined}
                      data-testid={"input-" + key}
                      value={currentValue}
                      onChange={(e) =>
                        setRuntimeDraft((prev) => ({ ...prev, [key]: e.target.value }))
                      }
                    />
                  )}
                  <span className="settings-source-badge">{sourceLabel(currentSource)}</span>
                </div>
              );
            })}
          </section>
          ) : null}
          {activeSection === "observability" ? <ObservabilityOverview /> : null}
            </div>

            {activeSection === "observability" ? null : (
            <div className="settings-modal-footer">
              <span>{hasDiff ? "有尚未保存的更改" : "所有更改已保存"}</span>
              <div>
                <button onClick={onClose}>取消</button>
                <button
                  className="settings-save-btn"
                  data-testid="settings-save"
                  disabled={!hasDiff}
                  onClick={handleSave}
                >
                  保存更改
                </button>
              </div>
            </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function sameStringList(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}
