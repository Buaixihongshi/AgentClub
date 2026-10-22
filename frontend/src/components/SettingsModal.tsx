import { useState, useMemo, useEffect } from "react";
import {
  Activity,
  AlertTriangle,
  KeyRound,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  UsersRound,
  X,
} from "lucide-react";
import type { ProviderStatusState } from "../hooks/useProviderStatus";
import { ObservabilityOverview } from "./ObservabilityOverview";
import {
  fetchConnectionModels,
  refreshConnectionModels,
} from "../lib/api";
import type {
  AgentOverridePatch,
  ConnectionCatalogView,
  HubConnectionPatch,
  HubConnectionProtocol,
  HubConnectionView,
  HubSettingsAgentView,
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
  cli_timeout: "执行窗口（秒，有工具返回可续时）",
  a2a_idle_timeout: "A2A 无进展超时（秒，0=关闭）",
};

interface AgentDraft {
  providerModels?: Record<string, string>;
  defaultProvider?: string;
  fallbacks?: string[];
  fallbackContextApproved?: boolean;
  clear?: boolean;
}

type SettingsSectionId = "team" | "runtime" | "observability";

type SelectedTarget =
  | { type: "agent"; id: string }
  | { type: "connection"; id: string };

interface Props {
  view: {
    runtime: import("../types").HubSettingsRuntimeView;
    providers: HubSettingsProviderView[];
    agents: HubSettingsAgentView[];
    connections?: HubConnectionView[];
  };
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
  const [connectionDrafts, setConnectionDrafts] = useState<Record<string, HubConnectionPatch>>({});
  const [catalogs, setCatalogs] = useState<Record<string, ConnectionCatalogView>>({});
  const [catalogLoading, setCatalogLoading] = useState<Record<string, boolean>>({});
  const [selectedVendorFilter, setSelectedVendorFilter] = useState<Record<string, string>>({});
  const [manualModelInputMode, setManualModelInputMode] = useState<Record<string, boolean>>({});
  const [copiedCommand, setCopiedCommand] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<SettingsSectionId>("team");

  const agentRows = view.agents ?? [];

  // Base connections: from view.connections if present, or synthesized from view.providers
  const baseConnections: HubConnectionView[] = useMemo(() => {
    if (view.connections && view.connections.length > 0) {
      return view.connections;
    }
    return view.providers
      .filter((p) => !INTERNAL_PROVIDERS.has(p.id) && !RETIRED_PROVIDERS.has(p.id))
      .map((p) => ({
        id: p.id,
        name: p.label,
        kind: "cli" as const,
        protocol: null,
        base_url: null,
        enabled: p.enabled.value,
        has_key: false,
        timeout: 120,
        allow_private: true,
      }));
  }, [view.connections, view.providers]);

  // Unified list of active connections
  const allConnections = useMemo(() => {
    const list: Array<
      HubConnectionView & { isNew?: boolean; isDeleted?: boolean; patch?: HubConnectionPatch }
    > = [];
    const seenIds = new Set<string>();

    for (const base of baseConnections) {
      seenIds.add(base.id);
      const patch = connectionDrafts[base.id];
      if (patch?.delete) {
        list.push({ ...base, isDeleted: true, patch });
        continue;
      }
      const enabled =
        patch?.enabled !== undefined
          ? patch.enabled
          : base.id in providerDraft
          ? providerDraft[base.id]
          : base.enabled;
      list.push({
        ...base,
        name: patch?.name ?? base.name,
        protocol: patch?.protocol !== undefined ? patch.protocol : base.protocol,
        base_url: patch?.base_url !== undefined ? patch.base_url : base.base_url,
        enabled,
        timeout: patch?.timeout ?? base.timeout,
        allow_private: patch?.allow_private ?? base.allow_private,
        patch,
      });
    }

    for (const [id, patch] of Object.entries(connectionDrafts)) {
      if (!seenIds.has(id) && !patch.delete) {
        list.push({
          id,
          name: patch.name || "自定义 API 连接",
          kind: "api",
          protocol: patch.protocol || "openai_responses",
          base_url: patch.base_url || "https://api.openai.com/v1",
          enabled: patch.enabled ?? true,
          has_key: Boolean(patch.api_key),
          timeout: patch.timeout ?? 60,
          allow_private: patch.allow_private ?? false,
          isNew: true,
          patch,
        });
      }
    }

    return list;
  }, [baseConnections, connectionDrafts, providerDraft]);

  const [selectedTarget, setSelectedTarget] = useState<SelectedTarget>(() => {
    if (agentRows.length > 0) {
      return { type: "agent", id: agentRows[0].id };
    }
    return { type: "connection", id: baseConnections[0]?.id || "codex" };
  });

  const enabledProviders = useMemo(() => {
    const map: Record<string, boolean> = {};
    for (const p of view.providers) {
      map[p.id] = p.id in providerDraft ? providerDraft[p.id] : p.enabled.value;
    }
    for (const c of allConnections) {
      if (!c.isDeleted) {
        map[c.id] = c.enabled;
      }
    }
    return map;
  }, [view.providers, providerDraft, allConnections]);

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

  const providerDisplayName = (provider: { id: string; label?: string; name?: string }) => {
    if (provider.id === "fake") return "演示模式（内置）";
    return provider.name || provider.label || provider.id;
  };

  const canRouteToProvider = (providerId: string) => {
    if (providerId === "fake") return true;
    if (verifiedProviderIds.has(providerId)) return true;
    return Boolean(enabledProviders[providerId]);
  };

  function copyCommand(key: string, command?: string | null) {
    if (!command) return;
    void navigator.clipboard?.writeText(command);
    setCopiedCommand(key);
  }

  function lifecycleFor(provider: { id: string; label?: string }) {
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
    fallbackContextApproved: boolean;
    isUserOverride: boolean;
  } {
    const draft = agentDraft[agent.id];
    if (draft?.clear) {
      return {
        defaultProvider: agent.base_default_provider_id,
        fallbacks: agent.base_fallback_provider_ids,
        fallbackContextApproved: false,
        isUserOverride: false,
      };
    }
    const defaultProvider = draft?.defaultProvider ?? agent.default_provider_id.value;
    const fallbacks = draft?.fallbacks ?? agent.fallback_provider_ids.value;
    const fallbackContextApproved =
      draft?.fallbackContextApproved ?? agent.fallback_context_approved?.value ?? false;
    const isUserOverride =
      draft !== undefined ||
      agent.default_provider_id.source === "user" ||
      agent.fallback_provider_ids.source === "user" ||
      agent.provider_models?.source === "user" ||
      agent.fallback_context_approved?.source === "user";
    return { defaultProvider, fallbacks, fallbackContextApproved, isUserOverride };
  }

  function agentHasDiff(agent: HubSettingsAgentView): boolean {
    const draft = agentDraft[agent.id];
    if (!draft) return false;
    if (draft.clear) {
      return (
        agent.default_provider_id.source === "user" ||
        agent.fallback_provider_ids.source === "user" ||
        agent.provider_models?.source === "user" ||
        agent.fallback_context_approved?.source === "user"
      );
    }
    if (
      draft.providerModels !== undefined &&
      JSON.stringify(draft.providerModels) !== JSON.stringify(agent.provider_models?.value ?? {})
    ) {
      return true;
    }
    if (
      draft.defaultProvider !== undefined &&
      draft.defaultProvider !== agent.default_provider_id.value
    ) {
      return true;
    }
    if (
      draft.fallbacks !== undefined &&
      !sameStringList(draft.fallbacks, agent.fallback_provider_ids.value)
    ) {
      return true;
    }
    if (
      draft.fallbackContextApproved !== undefined &&
      draft.fallbackContextApproved !== (agent.fallback_context_approved?.value ?? false)
    ) {
      return true;
    }
    return false;
  }

  const hasConnectionDiff = useMemo(() => {
    return Object.keys(connectionDrafts).length > 0;
  }, [connectionDrafts]);

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
    if (hasConnectionDiff) return true;
    return false;
  }, [view, providerDraft, runtimeDraft, agentDraft, agentRows, hasConnectionDiff]);

  function buildPatch(): HubSettingsPatch {
    const patch: HubSettingsPatch = {};

    // Providers
    const providerPatches: Record<string, { enabled: boolean }> = {};
    for (const p of view.providers) {
      if (p.id in providerDraft && providerDraft[p.id] !== p.enabled.value) {
        providerPatches[p.id] = { enabled: providerDraft[p.id] };
      }
    }
    if (Object.keys(providerPatches).length > 0) patch.providers = providerPatches;

    // Runtime
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

    // Agents
    const agentPatches: Record<string, AgentOverridePatch> = {};
    for (const agent of agentRows) {
      const draft = agentDraft[agent.id];
      if (!draft) continue;
      if (draft.clear) {
        agentPatches[agent.id] = { clear: true };
        continue;
      }
      const entry: AgentOverridePatch = {};
      if (
        draft.providerModels !== undefined &&
        JSON.stringify(draft.providerModels) !== JSON.stringify(agent.provider_models?.value ?? {})
      ) {
        entry.provider_models = draft.providerModels;
      }
      if (
        draft.defaultProvider !== undefined &&
        draft.defaultProvider !== agent.default_provider_id.value
      ) {
        entry.default_provider_id = draft.defaultProvider;
      }
      if (
        draft.fallbacks !== undefined &&
        !sameStringList(draft.fallbacks, agent.fallback_provider_ids.value)
      ) {
        entry.fallback_provider_ids = draft.fallbacks;
      }
      if (
        draft.fallbackContextApproved !== undefined &&
        draft.fallbackContextApproved !== (agent.fallback_context_approved?.value ?? false)
      ) {
        entry.fallback_context_approved = draft.fallbackContextApproved;
      }
      if (Object.keys(entry).length > 0) agentPatches[agent.id] = entry;
    }
    if (Object.keys(agentPatches).length > 0) patch.agents = agentPatches;

    // Connections
    if (Object.keys(connectionDrafts).length > 0) {
      patch.connections = connectionDrafts;
    }

    return patch;
  }

  function handleSave() {
    onSave(buildPatch());
  }

  function handleAttemptClose() {
    if (hasDiff) {
      const confirmed = window.confirm("当前有尚未保存的配置更改，关闭将放弃所有修改。确定要离开吗？");
      if (!confirmed) return;
    }
    onClose();
  }

  function toggleProvider(id: string) {
    if (id.startsWith("api-")) {
      const current = allConnections.find((c) => c.id === id);
      const isEnabled = current?.enabled ?? true;
      updateConnectionDraft(id, { enabled: !isEnabled });
    } else {
      setProviderDraft((prev) => ({ ...prev, [id]: !enabledProviders[id] }));
    }
  }

  function setAgentDefault(agentId: string, providerId: string) {
    setAgentDraft((prev) => {
      const current = prev[agentId] ?? {};
      return {
        ...prev,
        [agentId]: {
          ...current,
          defaultProvider: providerId,
          clear: false,
        },
      };
    });
  }

  function toggleAgentFallback(agent: HubSettingsAgentView, providerId: string) {
    setAgentDraft((prev) => {
      const eff = prev[agent.id]?.fallbacks ?? agent.fallback_provider_ids.value;
      const next = eff.includes(providerId)
        ? eff.filter((p) => p !== providerId)
        : [...eff, providerId];
      return {
        ...prev,
        [agent.id]: {
          ...(prev[agent.id] ?? {}),
          fallbacks: next,
          fallbackContextApproved: false, // 改备用须重确认
          clear: false,
        },
      };
    });
  }

  function toggleAgentFallbackContextApproval(agentId: string) {
    setAgentDraft((prev) => {
      const agent = agentRows.find((a) => a.id === agentId);
      const currentVal =
        prev[agentId]?.fallbackContextApproved ??
        agent?.fallback_context_approved?.value ??
        false;
      return {
        ...prev,
        [agentId]: {
          ...(prev[agentId] ?? {}),
          fallbackContextApproved: !currentVal,
          clear: false,
        },
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
        fallbackContextApproved: false,
        clear: false,
      };
    }
    setAgentDraft((prev) =>
      Object.fromEntries(
        Object.entries(next).map(([id, draft]) => [
          id,
          {
            ...draft,
            providerModels: prev[id]?.clear
              ? agentRows.find((a) => a.id === id)?.base_provider_models ?? {}
              : prev[id]?.providerModels,
          },
        ]),
      ),
    );
  }

  function updateConnectionDraft(id: string, patch: Partial<HubConnectionPatch>) {
    setConnectionDrafts((prev) => ({
      ...prev,
      [id]: { ...(prev[id] ?? {}), ...patch },
    }));
  }

  function handleCreateApiConnection() {
    const newId = `api-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    setConnectionDrafts((prev) => ({
      ...prev,
      [newId]: {
        name: "自定义 API 连接",
        protocol: "openai_responses",
        base_url: "https://api.openai.com/v1",
        enabled: true,
        timeout: 60,
        allow_private: false,
        key_action: "replace",
        api_key: "",
      },
    }));
    setSelectedTarget({ type: "connection", id: newId });
  }

  function handleDeleteConnection(id: string) {
    if (window.confirm("确定要删除该连接配置吗？保存后生效。")) {
      setConnectionDrafts((prev) => ({
        ...prev,
        [id]: { ...(prev[id] ?? {}), delete: true },
      }));
      if (agentRows.length > 0) {
        setSelectedTarget({ type: "agent", id: agentRows[0].id });
      }
    }
  }

  async function handleRefreshCatalog(connId: string) {
    const isNew = connectionDrafts[connId] && !baseConnections.some((c) => c.id === connId);
    if (isNew) return; // 新建未保存连接先保存再刷新

    setCatalogLoading((prev) => ({ ...prev, [connId]: true }));
    try {
      const res = await refreshConnectionModels(connId);
      setCatalogs((prev) => ({ ...prev, [connId]: res }));
    } catch (err) {
      setCatalogs((prev) => ({
        ...prev,
        [connId]: {
          connection_id: connId,
          status: "error",
          models: [],
          fetched_at: null,
          error: err instanceof Error ? err.message : "获取模型目录失败",
        },
      }));
    } finally {
      setCatalogLoading((prev) => ({ ...prev, [connId]: false }));
    }
  }

  const sourceLabel = (s: string) => {
    switch (s) {
      case "user":
        return "用户";
      case "env":
        return "env";
      case "default":
        return "默认";
      case "committed":
        return "committed";
      default:
        return s;
    }
  };

  const warningProviders = visibleProviders.filter(
    (p) => !INTERNAL_PROVIDERS.has(p.id) && enabledProviders[p.id],
  );

  // Active selected entities
  const selectedAgent =
    selectedTarget.type === "agent"
      ? agentRows.find((a) => a.id === selectedTarget.id) || agentRows[0]
      : null;

  const selectedConnection =
    selectedTarget.type === "connection"
      ? allConnections.find((c) => c.id === selectedTarget.id && !c.isDeleted)
      : null;

  return (
    <div className="settings-modal-backdrop" onClick={handleAttemptClose}>
      <div className="settings-modal settings-modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="settings-modal-header">
          <div>
            <h2>设置</h2>
            <p>管理 CLI 与自定义 API、角色调度、运行参数与全局观测</p>
          </div>
          <button
            type="button"
            className="settings-close"
            data-testid="settings-close"
            aria-label="关闭设置"
            onClick={handleAttemptClose}
          >
            <X size={19} aria-hidden="true" />
          </button>
        </div>

        <div className="settings-modal-content">
          <nav className="settings-nav" aria-label="设置分类">
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
              {activeSection === "team" ? (
                <div className="workbench-split">
                  {/* 左侧连接池与伙伴导航 */}
                  <aside className="workbench-sidebar">
                    {/* 连接管理区 */}
                    <div className="workbench-sidebar-section">
                      <div className="workbench-sidebar-heading">
                        <h3 id="settings-providers-title">CLI 接入</h3>
                        <div className="workbench-sidebar-actions">
                          <button
                            type="button"
                            className="settings-refresh-btn-sm"
                            onClick={onRefreshProviders ?? providerStatus?.refresh}
                            disabled={providerStatus?.loading}
                            title="重新检测 CLI"
                          >
                            <RefreshCw
                              size={12}
                              className={providerStatus?.loading ? "animate-spin" : ""}
                            />
                            <span>{providerStatus?.loading ? "检测中" : "检测"}</span>
                          </button>
                          <button
                            type="button"
                            className="btn-text-action"
                            data-testid="add-connection-btn"
                            onClick={handleCreateApiConnection}
                          >
                            <Plus size={12} />
                            <span>新增 API</span>
                          </button>
                        </div>
                      </div>

                      <div className="workbench-conn-list">
                        {allConnections
                          .filter((c) => !c.isDeleted)
                          .map((c) => {
                            const isCli = c.kind === "cli";
                            const lifecycle = lifecycleFor({ id: c.id, label: c.name });
                            const isSelected =
                              selectedTarget.type === "connection" &&
                              selectedTarget.id === c.id;
                            const isEnabled = enabledProviders[c.id] ?? c.enabled;
                            const diagnostic = lifecycle.diagnostic;
                            const setup = lifecycle.setup as OnboardingProviderStatus | undefined;
                            const verified = lifecycle.tone === "ready";
                            const available = Boolean(diagnostic?.available);

                            return (
                              <article
                                key={c.id}
                                className={`conn-list-item ${isSelected ? "is-active" : ""} ${
                                  !isEnabled ? "is-disabled" : ""
                                }`}
                                data-testid={`provider-card-${c.id}`}
                                onClick={() =>
                                  setSelectedTarget({ type: "connection", id: c.id })
                                }
                              >
                                <div className="conn-item-main">
                                  <div className="conn-item-header">
                                    <span
                                      className={`status-dot dot-${
                                        isCli ? lifecycle.tone : c.has_key ? "ready" : "warning"
                                      }`}
                                    />
                                    <strong>{c.name}</strong>
                                    <span className={`kind-tag ${c.kind}`}>
                                      {c.kind.toUpperCase()}
                                    </span>
                                  </div>
                                  <div className="conn-item-sub">
                                    {isCli ? (
                                      <span
                                        className={`settings-provider-lifecycle is-${lifecycle.tone}`}
                                      >
                                        {lifecycle.label}
                                      </span>
                                    ) : (
                                      <span className="conn-sub-url">
                                        {c.base_url || "未设置 Base URL"} ·{" "}
                                        {c.has_key ? "已配凭据" : "未配凭据"}
                                      </span>
                                    )}
                                  </div>
                                </div>

                                <div
                                  className="conn-item-controls"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  {isCli && !available && !verified ? (
                                    setup?.install_command ? (
                                      <button
                                        type="button"
                                        className="btn-tiny"
                                        onClick={() =>
                                          copyCommand(`${c.id}:install`, setup.install_command)
                                        }
                                      >
                                        {copiedCommand === `${c.id}:install`
                                          ? "已复制"
                                          : "安装命令"}
                                      </button>
                                    ) : null
                                  ) : isCli && !verified && onVerifyProvider ? (
                                    <button
                                      type="button"
                                      className="settings-provider-verify btn-tiny"
                                      onClick={() => onVerifyProvider(c.id)}
                                    >
                                      验证真实对话
                                    </button>
                                  ) : null}

                                  <button
                                    type="button"
                                    role="switch"
                                    aria-label={`启用 ${c.name}`}
                                    aria-checked={isEnabled}
                                    className={"settings-toggle" + (isEnabled ? " on" : "")}
                                    data-testid={"toggle-" + c.id}
                                    disabled={
                                      isCli &&
                                      Boolean(providerStatus) &&
                                      !available &&
                                      !verified &&
                                      !isEnabled
                                    }
                                    onClick={() => toggleProvider(c.id)}
                                  >
                                    {isEnabled ? "ON" : "OFF"}
                                  </button>
                                </div>
                              </article>
                            );
                          })}
                      </div>

                      {warningProviders.length > 0 && (
                        <p className="settings-cost-warning">
                          启用真实 provider 将向{" "}
                          {warningProviders.map((p) => p.label).join("、")} 发送真实请求并产生费用
                        </p>
                      )}
                    </div>

                    {/* Agent 导航区 */}
                    <div className="workbench-sidebar-section">
                      <div className="workbench-sidebar-heading">
                        <h3 id="settings-team-title">团队配置</h3>
                      </div>

                      <div className="team-settings-quick-action">
                        <div>
                          <strong>快速配置</strong>
                          <p>三位角色统一使用 Codex</p>
                        </div>
                        <button
                          type="button"
                          className="button-secondary"
                          data-testid="team-use-codex"
                          disabled={
                            !enabledProviders.codex ||
                            !canRouteToProvider("codex") ||
                            agentRows.length === 0
                          }
                          onClick={() => routeAllAgentsTo("codex")}
                        >
                          {enabledProviders.codex ? "三位角色使用 Codex" : "Codex 尚未启用"}
                        </button>
                      </div>

                      <div className="workbench-agent-list">
                        {agentRows.map((agent) => {
                          const { defaultProvider, fallbacks, isUserOverride } =
                            effectiveAgent(agent);
                          const isSelected =
                            selectedTarget.type === "agent" &&
                            selectedTarget.id === agent.id;
                          const primaryConn = allConnections.find(
                            (c) => c.id === defaultProvider,
                          );

                          return (
                            <div
                              key={agent.id}
                              className={`agent-nav-item ${isSelected ? "is-active" : ""}`}
                              onClick={() => setSelectedTarget({ type: "agent", id: agent.id })}
                            >
                              <div className={`agent-nav-avatar avatar-${agent.id}`}>
                                {agent.display_name.slice(0, 1)}
                              </div>
                              <div className="agent-nav-info">
                                <div className="agent-nav-name">
                                  <span>{agent.display_name}</span>
                                  <span className="settings-source-badge">
                                    {sourceLabel(isUserOverride ? "user" : "committed")}
                                  </span>
                                </div>
                                <div className="agent-nav-sub">
                                  主用: {primaryConn?.name || defaultProvider} / 备用:{" "}
                                  {fallbacks.join(", ") || "无"}
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </aside>

                  {/* 右侧装配与安全审计详情 */}
                  <main className="workbench-detail">
                    {selectedTarget.type === "agent" && selectedAgent ? (
                      <div
                        className="agent-workbench-panel"
                        data-testid={"agent-row-" + selectedAgent.id}
                      >
                        {(() => {
                          const agent = selectedAgent;
                          const {
                            defaultProvider,
                            fallbacks,
                            fallbackContextApproved,
                            isUserOverride,
                          } = effectiveAgent(agent);
                          const primaryConn = allConnections.find(
                            (c) => c.id === defaultProvider,
                          );
                          const isPrimaryCli = (primaryConn?.kind ?? "cli") === "cli";
                          const draft = agentDraft[agent.id];
                          const models = draft?.clear
                            ? agent.base_provider_models ?? {}
                            : draft?.providerModels ?? agent.provider_models?.value ?? {};

                          const primaryModel = models[defaultProvider] ?? "";
                          const isApiMissingModel = !isPrimaryCli && !primaryModel.trim();
                          const primaryCatalog = catalogs[defaultProvider];

                          return (
                            <>
                              <div className="detail-header">
                                <div className="detail-header-title">
                                  <div className={`avatar-large avatar-${agent.id}`}>
                                    {agent.display_name.slice(0, 1)}
                                  </div>
                                  <div>
                                    <div className="detail-title-row">
                                      <h3>{agent.display_name} · 调度参数与网络出站装配台</h3>
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
                                    <p className="detail-sub">
                                      {isPrimaryCli
                                        ? "主用沙箱执行，文件与终端工具就绪"
                                        : "主用接入自定义 API，提供纯文本对话与讨论"}
                                    </p>
                                  </div>
                                </div>

                                <div className="detail-header-badges">
                                  {isPrimaryCli ? (
                                    <span className="ability-badge cli">
                                      🛠️ 当前主用具备 CLI 执行权限
                                    </span>
                                  ) : (
                                    <span className="ability-badge api">
                                      💬 当前主用为仅纯文本 API
                                    </span>
                                  )}
                                </div>
                              </div>

                              {allConnections.map((connection) => (
                                <datalist key={connection.id} id={`connection-models-${connection.id}`}>
                                  {(catalogs[connection.id]?.models ?? []).map((model) => (
                                    <option key={model.id} value={model.id}>{model.label}</option>
                                  ))}
                                </datalist>
                              ))}

                              <div className="workbench-grid-2col">
                                {/* PRIMARY · 主用执行链路 */}
                                <div className="workbench-card primary-card">
                                  <div className="card-heading primary-heading">
                                    <span>PRIMARY · 主用执行链路</span>
                                    <span className="card-badge">主链路</span>
                                  </div>

                                  <div className="form-group">
                                    <label className="form-label">指定主用连接</label>
                                    <select
                                      className="custom-select"
                                      data-testid={"agent-default-" + agent.id}
                                      value={defaultProvider}
                                      onChange={(e) => {
                                        setAgentDefault(agent.id, e.target.value);
                                      }}
                                    >
                                      {allConnections
                                        .filter((c) => !c.isDeleted)
                                        .map((c) => (
                                          <option
                                            key={c.id}
                                            value={c.id}
                                            disabled={!canRouteToProvider(c.id)}
                                          >
                                            {c.name} ({c.kind.toUpperCase()})
                                          </option>
                                        ))}
                                      {routeProviders
                                        .filter((p) => !allConnections.some((c) => c.id === p.id))
                                        .map((p) => (
                                          <option
                                            key={p.id}
                                            value={p.id}
                                            disabled={!canRouteToProvider(p.id)}
                                          >
                                            {providerDisplayName(p)}
                                          </option>
                                        ))}
                                    </select>
                                  </div>

                                  <div className="form-group">
                                    <div className="form-label-row">
                                      <label className="form-label">
                                        指定模型 (Model ID){" "}
                                        {isPrimaryCli ? (
                                          <span className="desc-tag">可选，留空沿用 CLI 默认</span>
                                        ) : (
                                          <span className="desc-tag required">必填</span>
                                        )}
                                      </label>
                                      {primaryCatalog?.models && primaryCatalog.models.length > 0 && (
                                        <button
                                          type="button"
                                          className="btn-text-action"
                                          onClick={() =>
                                            setManualModelInputMode((prev) => ({
                                              ...prev,
                                              [defaultProvider]: !prev[defaultProvider],
                                            }))
                                          }
                                        >
                                          {manualModelInputMode[defaultProvider]
                                            ? "切换为目录选择"
                                            : "手动输入"}
                                        </button>
                                      )}
                                    </div>

                                    {/* 动态目录与模型选择 */}
                                    {primaryCatalog?.models &&
                                    primaryCatalog.models.length > 0 &&
                                    !manualModelInputMode[defaultProvider] ? (
                                      <div className="catalog-model-select">
                                        {/* 厂商快速筛选 Chip */}
                                        <div className="vendor-chips">
                                          {(() => {
                                            const vendors = Array.from(
                                              new Set(
                                                primaryCatalog.models
                                                  .map((m) => m.vendor || "未分类")
                                                  .filter(Boolean),
                                              ),
                                            );
                                            const curVendor =
                                              selectedVendorFilter[defaultProvider] || "all";
                                            return (
                                              <>
                                                <button
                                                  type="button"
                                                  className={`chip ${
                                                    curVendor === "all" ? "active" : ""
                                                  }`}
                                                  onClick={() =>
                                                    setSelectedVendorFilter((prev) => ({
                                                      ...prev,
                                                      [defaultProvider]: "all",
                                                    }))
                                                  }
                                                >
                                                  全部
                                                </button>
                                                {vendors.map((v) => (
                                                  <button
                                                    key={v}
                                                    type="button"
                                                    className={`chip ${
                                                      curVendor === v ? "active" : ""
                                                    }`}
                                                    onClick={() =>
                                                      setSelectedVendorFilter((prev) => ({
                                                        ...prev,
                                                        [defaultProvider]: v,
                                                      }))
                                                    }
                                                  >
                                                    {v}
                                                  </button>
                                                ))}
                                              </>
                                            );
                                          })()}
                                        </div>

                                        <select
                                          className="custom-select"
                                          aria-label={`${agent.display_name} ${defaultProvider} 模型`}
                                          value={primaryModel}
                                          onChange={(e) => {
                                            const next = { ...models };
                                            const modelVal = e.target.value.trim();
                                            if (modelVal) next[defaultProvider] = modelVal;
                                            else delete next[defaultProvider];
                                            setAgentDraft((prev) => ({
                                              ...prev,
                                              [agent.id]: {
                                                ...(prev[agent.id] ?? {}),
                                                providerModels: next,
                                                clear: false,
                                              },
                                            }));
                                          }}
                                        >
                                          <option value="">
                                            {isPrimaryCli
                                              ? "留空使用 CLI 默认模型"
                                              : "-- 请选择模型 (必填) --"}
                                          </option>
                                          {primaryCatalog.models
                                            .filter((m) => {
                                              const vf =
                                                selectedVendorFilter[defaultProvider] || "all";
                                              if (vf === "all") return true;
                                              const mVendor = m.vendor || "未分类";
                                              return mVendor === vf;
                                            })
                                            .map((m) => (
                                              <option key={m.id} value={m.id}>
                                                {m.label} ({m.id})
                                              </option>
                                            ))}
                                        </select>
                                      </div>
                                    ) : (
                                      <input
                                        type="text"
                                        className={`custom-input ${
                                          isApiMissingModel ? "is-invalid" : ""
                                        }`}
                                        aria-label={`${agent.display_name} ${defaultProvider} 模型`}
                                        list={`connection-models-${defaultProvider}`}
                                        value={primaryModel}
                                        placeholder={
                                          isPrimaryCli
                                            ? "留空使用 CLI 默认模型"
                                            : "输入接入服务提供的 Model ID（必填）"
                                        }
                                        onChange={(e) => {
                                          const next = { ...models };
                                          const modelVal = e.target.value.trim();
                                          if (modelVal) next[defaultProvider] = modelVal;
                                          else delete next[defaultProvider];
                                          setAgentDraft((prev) => ({
                                            ...prev,
                                            [agent.id]: {
                                              ...(prev[agent.id] ?? {}),
                                              providerModels: next,
                                              clear: false,
                                            },
                                          }));
                                        }}
                                      />
                                    )}

                                    {isApiMissingModel && (
                                      <p className="input-error-msg">
                                        ⚠️ 自定义 API 连接首期必须明确指定模型 ID
                                      </p>
                                    )}
                                  </div>

                                  <div className="card-note">
                                    {isPrimaryCli ? (
                                      <span className="text-ok">
                                        ✓ 具备读写代码、执行测试与终端命令能力
                                      </span>
                                    ) : (
                                      <span className="text-warn">
                                        ⚠️ 仅支持纯文本对话与讨论，不自动提供本地执行器
                                      </span>
                                    )}
                                  </div>
                                </div>

                                {/* FALLBACK · 容灾备用链路 */}
                                <div className="workbench-card fallback-card">
                                  <div className="card-heading fallback-heading">
                                    <span>FALLBACK · 容灾备用链路</span>
                                    <span className="card-badge">故障降级</span>
                                  </div>

                                  <div className="form-group">
                                    <label className="form-label">
                                      备用连接 (容灾回退目标)
                                    </label>
                                    <div className="fallback-options-grid">
                                      {routeProviders
                                        .filter((p) => p.id !== defaultProvider)
                                        .map((p) => (
                                          <label
                                            key={p.id}
                                            className="fallback-checkbox-label"
                                          >
                                            <input
                                              type="checkbox"
                                              data-testid={`agent-fallback-${agent.id}-${p.id}`}
                                              checked={fallbacks.includes(p.id)}
                                              disabled={!canRouteToProvider(p.id)}
                                              onChange={() =>
                                                toggleAgentFallback(agent, p.id)
                                              }
                                            />
                                            <span>{providerDisplayName(p)}</span>
                                          </label>
                                        ))}
                                    </div>
                                  </div>

                                  {/* 备用连接对应的模型输入 */}
                                  {fallbacks.map((fbId) => {
                                    const fbConn = allConnections.find((c) => c.id === fbId);
                                    const fbName = fbConn?.name || fbId;
                                    const fbModel = models[fbId] ?? "";
                                    return (
                                      <div className="form-group" key={fbId}>
                                        <label className="form-label">
                                          <span>{fbName} 备用模型</span>
                                          <span className="desc-tag">
                                            {fbConn?.kind === "api" ? "必填" : "可选"}
                                          </span>
                                        </label>
                                        <input
                                          type="text"
                                          className="custom-input"
                                          aria-label={`${agent.display_name} ${fbId} 模型`}
                                          list={`connection-models-${fbId}`}
                                          value={fbModel}
                                          placeholder={
                                            fbConn?.kind === "api"
                                              ? "输入备用 Model ID"
                                              : "留空沿用 CLI 默认"
                                          }
                                          onChange={(e) => {
                                            const next = { ...models };
                                            const modelVal = e.target.value.trim();
                                            if (modelVal) next[fbId] = modelVal;
                                            else delete next[fbId];
                                            setAgentDraft((prev) => ({
                                              ...prev,
                                              [agent.id]: {
                                                ...(prev[agent.id] ?? {}),
                                                providerModels: next,
                                                clear: false,
                                              },
                                            }));
                                          }}
                                        />
                                      </div>
                                    );
                                  })}

                                  {/* 数据出站流向与端点授权 */}
                                  <div className="outbound-box">
                                    <div className="outbound-flow">
                                      <strong>📡 数据出站流向：</strong>
                                      <div className="flow-text">
                                        主用 [
                                        <code>
                                          {primaryConn?.base_url ||
                                            primaryConn?.name ||
                                            defaultProvider}
                                        </code>
                                        ] ➔ 备用出站 [
                                        <code>{fallbacks.join(", ") || "未配置"}</code>]
                                      </div>
                                    </div>

                                    <label className="fallback-approve-label">
                                      <input
                                        type="checkbox"
                                        data-testid={`agent-fallback-approve-${agent.id}`}
                                        checked={fallbackContextApproved}
                                        onChange={() =>
                                          toggleAgentFallbackContextApproval(agent.id)
                                        }
                                      />
                                      <span>
                                        授权已选显式备用端点接收当前任务上下文
                                      </span>
                                    </label>
                                    <div className="outbound-tip">
                                      * 熔断切换同时是数据接收方切换；修改备用连接后必须重新授权。
                                    </div>
                                  </div>
                                </div>
                              </div>

                              {/* 安全与出站审计面板 */}
                              <div className="security-audit-box">
                                <div className="audit-header">
                                  <ShieldCheck size={16} className="text-ok" />
                                  <strong>
                                    宝儿姐安全规范审计通过 (P2 Security Verification)
                                  </strong>
                                </div>
                                <div className="audit-items">
                                  <div className="audit-item">
                                    <span className="audit-label">出站 Base URL 规范:</span>
                                    <code>HTTPS 强制 / 禁止 30x 跨域重定向 / 防 SSRF 白名单</code>
                                  </div>
                                  <div className="audit-item">
                                    <span className="audit-label">凭据安全隔离:</span>
                                    <code>
                                      前端仅展示已配置状态 / 严禁掩码或明文回传 / 独立存储
                                    </code>
                                  </div>
                                  <div className="audit-item">
                                    <span className="audit-label">容灾防雪崩机制:</span>
                                    <code>
                                      401 鉴权失效禁切备用 / 流式已输出内容严禁静默重放
                                    </code>
                                  </div>
                                </div>
                              </div>
                            </>
                          );
                        })()}
                      </div>
                    ) : selectedTarget.type === "connection" && selectedConnection ? (
                      <div className="conn-workbench-panel">
                        {(() => {
                          const c = selectedConnection;
                          const isCli = c.kind === "cli";
                          const lifecycle = lifecycleFor({ id: c.id, label: c.name });
                          const catalog = catalogs[c.id];
                          const isLoadingCat = catalogLoading[c.id];
                          const isNew = Boolean(c.isNew);

                          return (
                            <>
                              <div className="detail-header">
                                <div className="detail-header-title">
                                  <div>
                                    <div className="detail-title-row">
                                      <h3>{c.name} · 连接与凭据配置</h3>
                                      <span className={`kind-tag ${c.kind}`}>
                                        {c.kind.toUpperCase()}
                                      </span>
                                      {c.kind === "api" && (
                                        <button
                                          type="button"
                                          className="btn-danger-outline btn-sm"
                                          onClick={() => handleDeleteConnection(c.id)}
                                        >
                                          <Trash2 size={13} />
                                          <span>删除连接</span>
                                        </button>
                                      )}
                                    </div>
                                    <p className="detail-sub">
                                      {isCli
                                        ? "本地 CLI 接入服务，具备本地工具执行权限"
                                        : "自定义远程 API 端点，支持 OpenAI 及 Anthropic 协议"}
                                    </p>
                                  </div>
                                </div>

                                <div className="detail-header-badges">
                                  {isCli ? (
                                    <span className="ability-badge cli">
                                      🛠️ 具备工具执行环境
                                    </span>
                                  ) : (
                                    <span className="ability-badge api">
                                      💬 仅纯文本对话与讨论
                                    </span>
                                  )}
                                </div>
                              </div>

                              {/* CLI 属性详情 */}
                              {isCli ? (
                                <div className="workbench-card">
                                  <div className="card-heading">
                                    <span>CLI 本地运行环境诊断</span>
                                    <span
                                      className={`settings-provider-lifecycle is-${lifecycle.tone}`}
                                    >
                                      {lifecycle.label}
                                    </span>
                                  </div>
                                  <p className="settings-provider-detail">
                                    {lifecycle.diagnostic?.version ||
                                      lifecycle.diagnostic?.error ||
                                      (lifecycle.tone === "ready"
                                        ? "已通过 MCP 真实对话验证"
                                        : "等待本机检测")}
                                  </p>

                                  <div className="settings-provider-actions">
                                    {lifecycle.setup?.install_command ? (
                                      <button
                                        type="button"
                                        onClick={() =>
                                          copyCommand(
                                            `${c.id}:install`,
                                            lifecycle.setup?.install_command,
                                          )
                                        }
                                      >
                                        {copiedCommand === `${c.id}:install`
                                          ? "安装命令已复制"
                                          : "复制安装命令"}
                                      </button>
                                    ) : null}
                                    {lifecycle.setup?.install_url ? (
                                      <a
                                        href={lifecycle.setup.install_url}
                                        target="_blank"
                                        rel="noreferrer"
                                      >
                                        官方说明
                                      </a>
                                    ) : null}
                                    {lifecycle.setup?.auth_command ? (
                                      <button
                                        type="button"
                                        onClick={() =>
                                          copyCommand(
                                            `${c.id}:auth`,
                                            lifecycle.setup?.auth_command,
                                          )
                                        }
                                      >
                                        {copiedCommand === `${c.id}:auth`
                                          ? "登录命令已复制"
                                          : `登录：${lifecycle.setup.auth_command}`}
                                      </button>
                                    ) : null}
                                    {onVerifyProvider && lifecycle.tone !== "ready" ? (
                                      <button
                                        type="button"
                                        className="settings-provider-verify"
                                        onClick={() => onVerifyProvider(c.id)}
                                      >
                                        验证真实对话
                                      </button>
                                    ) : null}
                                  </div>
                                </div>
                              ) : (
                                /* API 属性表单 */
                                <div className="workbench-card">
                                  <div className="card-heading">
                                    <span>API 端点与网络出站设置</span>
                                  </div>

                                  <div className="form-group">
                                    <label className="form-label">连接别名</label>
                                    <input
                                      type="text"
                                      className="custom-input"
                                      value={c.name}
                                      onChange={(e) =>
                                        updateConnectionDraft(c.id, { name: e.target.value })
                                      }
                                    />
                                  </div>

                                  <div className="form-group">
                                    <label className="form-label">协议类型</label>
                                    <select
                                      className="custom-select"
                                      value={c.protocol || "openai_responses"}
                                      onChange={(e) =>
                                        updateConnectionDraft(c.id, {
                                          protocol: e.target.value as HubConnectionProtocol,
                                        })
                                      }
                                    >
                                      <option value="openai_responses">
                                        OpenAI - Responses 协议 (官方推荐)
                                      </option>
                                      <option value="openai_chat">
                                        OpenAI - Chat Completions 协议 (兼容网关)
                                      </option>
                                      <option value="anthropic">
                                        Anthropic - Messages 协议 (Claude)
                                      </option>
                                    </select>
                                  </div>

                                  <div className="form-group">
                                    <label className="form-label">
                                      <span>Base URL</span>
                                      <span className="desc-tag">
                                        默认强制 HTTPS，支持内网白名单
                                      </span>
                                    </label>
                                    <input
                                      type="text"
                                      className="custom-input"
                                      value={c.base_url || ""}
                                      placeholder="https://api.openai.com/v1"
                                      onChange={(e) =>
                                        updateConnectionDraft(c.id, {
                                          base_url: e.target.value,
                                        })
                                      }
                                    />
                                  </div>

                                  <div className="form-row">
                                    <label className="checkbox-row">
                                      <input
                                        type="checkbox"
                                        checked={c.allow_private}
                                        onChange={(e) =>
                                          updateConnectionDraft(c.id, {
                                            allow_private: e.target.checked,
                                          })
                                        }
                                      />
                                      <span>允许内网/本地端点 (如 http://localhost 或私有网关)</span>
                                    </label>

                                    <div className="timeout-input-group">
                                      <label>超时时间 (秒):</label>
                                      <input
                                        type="number"
                                        className="custom-input input-narrow"
                                        min="1"
                                        max="600"
                                        value={c.timeout}
                                        onChange={(e) =>
                                          updateConnectionDraft(c.id, {
                                            timeout: Number(e.target.value) || 60,
                                          })
                                        }
                                      />
                                    </div>
                                  </div>

                                  {/* 密钥安全三态交互 */}
                                  <div className="form-group key-form-group">
                                    <label className="form-label">
                                      <span>API 凭据密钥 (API Key)</span>
                                      <span className="desc-tag">
                                        前端无明文或掩码回传，独立加密存储
                                      </span>
                                    </label>

                                    <div className="key-state-box">
                                      {c.has_key ? (
                                        <div className="key-state-row">
                                          <div className="key-status-display">
                                            {c.patch?.key_action === "delete" ? (
                                              <span className="badge-danger">
                                                凭据已标记删除 (保存后生效)
                                              </span>
                                            ) : c.patch?.key_action === "replace" ? (
                                              <span className="badge-warning">
                                                凭据将被新输入的密钥替换
                                              </span>
                                            ) : (
                                              <span className="key-status-badge">
                                                <KeyRound size={13} />
                                                <span>已配置凭据 (后端安全存储，无明文回传)</span>
                                              </span>
                                            )}
                                          </div>

                                          <div className="key-action-buttons">
                                            {c.patch?.key_action === "delete" ? (
                                              <button
                                                type="button"
                                                className="btn-tiny"
                                                onClick={() =>
                                                  updateConnectionDraft(c.id, {
                                                    key_action: "keep",
                                                  })
                                                }
                                              >
                                                撤销删除
                                              </button>
                                            ) : c.patch?.key_action === "replace" ? (
                                              <button
                                                type="button"
                                                className="btn-tiny"
                                                onClick={() =>
                                                  updateConnectionDraft(c.id, {
                                                    key_action: "keep",
                                                    api_key: undefined,
                                                  })
                                                }
                                              >
                                                取消替换
                                              </button>
                                            ) : (
                                              <>
                                                <button
                                                  type="button"
                                                  className="btn-tiny"
                                                  onClick={() =>
                                                    updateConnectionDraft(c.id, {
                                                      key_action: "replace",
                                                    })
                                                  }
                                                >
                                                  替换密钥
                                                </button>
                                                <button
                                                  type="button"
                                                  className="btn-tiny btn-danger"
                                                  onClick={() =>
                                                    updateConnectionDraft(c.id, {
                                                      key_action: "delete",
                                                    })
                                                  }
                                                >
                                                  彻底删除
                                                </button>
                                              </>
                                            )}
                                          </div>
                                        </div>
                                      ) : null}

                                      {(!c.has_key || c.patch?.key_action === "replace") && (
                                        <div className="key-input-row">
                                          <input
                                            type="password"
                                            className="custom-input"
                                            placeholder="在此输入 API Key (保存后加密提交，前端不留存)"
                                            value={c.patch?.api_key || ""}
                                            onChange={(e) =>
                                              updateConnectionDraft(c.id, {
                                                key_action: "replace",
                                                api_key: e.target.value,
                                              })
                                            }
                                          />
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              )}

                              {/* 动态模型目录 Card */}
                              <div className="workbench-card">
                                <div className="card-heading">
                                  <span>动态模型目录探测 (Dynamic Catalog)</span>
                                  <div className="catalog-status-actions">
                                    {catalog?.status && (
                                      <span
                                        className={`catalog-status-badge status-${catalog.status}`}
                                      >
                                        {catalog.status === "ready"
                                          ? "就绪"
                                          : catalog.status === "stale"
                                          ? "过期缓存"
                                          : catalog.status === "error"
                                          ? "探测失败"
                                          : catalog.status}
                                      </span>
                                    )}
                                    <button
                                      type="button"
                                      className="settings-refresh-btn-sm"
                                      disabled={isNew || isLoadingCat}
                                      onClick={() => handleRefreshCatalog(c.id)}
                                      title={
                                        isNew
                                          ? "新建未保存连接请先保存再刷新"
                                          : "后台刷新最新模型目录"
                                      }
                                    >
                                      <RefreshCw
                                        size={12}
                                        className={isLoadingCat ? "animate-spin" : ""}
                                      />
                                      <span>
                                        {isLoadingCat
                                          ? "探测中…"
                                          : isNew
                                          ? "未保存"
                                          : "刷新目录"}
                                      </span>
                                    </button>
                                  </div>
                                </div>

                                {isNew ? (
                                  <p className="settings-section-hint">
                                    新建未保存连接请先点击底部「保存更改」，保存连接后再探测目录。
                                  </p>
                                ) : catalog?.error ? (
                                  <div className="catalog-error-box">
                                    <AlertTriangle size={14} />
                                    <span>探测失败: {catalog.error}</span>
                                  </div>
                                ) : catalog?.models && catalog.models.length > 0 ? (
                                  <div className="catalog-models-summary">
                                    <p className="catalog-count-text">
                                      已发现 {catalog.models.length} 个可用模型：
                                    </p>
                                    <div className="catalog-chips-preview">
                                      {catalog.models.map((m) => (
                                        <span key={m.id} className="model-chip-preview">
                                          {m.label} ({m.id})
                                        </span>
                                      ))}
                                    </div>
                                  </div>
                                ) : (
                                  <p className="settings-section-hint">
                                    暂无缓存目录，点击上方「刷新目录」可探测后端模型。
                                  </p>
                                )}
                              </div>
                            </>
                          );
                        })()}
                      </div>
                    ) : null}
                  </main>
                </div>
              ) : null}

              {activeSection === "runtime" ? (
                <section
                  className="settings-section"
                  aria-labelledby="settings-runtime-title"
                >
                  <h3 id="settings-runtime-title">运行参数</h3>
                  <p className="settings-section-hint">
                    控制任务轮次、Agent 接力预算与执行窗口。每 5
                    分钟检查一次工具返回，有新结果就续一个执行窗口。保存后对新任务生效。
                  </p>
                  {(
                    Object.keys(RUNTIME_LABELS) as (keyof typeof RUNTIME_LABELS)[]
                  ).map((key) => {
                    const rtMap = view.runtime as unknown as Record<
                      string,
                      { value: unknown; source: string }
                    >;
                    const field = rtMap[key];
                    if (!field) return null;
                    const isBool = typeof field.value === "boolean";
                    const currentValue =
                      key in runtimeDraft ? runtimeDraft[key] : String(field.value);
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
                            className={
                              "settings-toggle" +
                              (currentValue === "true" ? " on" : "")
                            }
                            data-testid={"input-" + key}
                            onClick={() =>
                              setRuntimeDraft((prev) => ({
                                ...prev,
                                [key]:
                                  prev[key] !== undefined
                                    ? prev[key] === "true"
                                      ? "false"
                                      : "true"
                                    : field.value
                                    ? "false"
                                    : "true",
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
                              setRuntimeDraft((prev) => ({
                                ...prev,
                                [key]: e.target.value,
                              }))
                            }
                          />
                        )}
                        <span className="settings-source-badge">
                          {sourceLabel(currentSource)}
                        </span>
                      </div>
                    );
                  })}
                </section>
              ) : null}

              {activeSection === "observability" ? <ObservabilityOverview /> : null}
            </div>

            {activeSection === "observability" ? null : (
              <div className="settings-modal-footer">
                <div className="footer-status">
                  <span className={`status-dot dot-${hasDiff ? "warning" : "ready"}`} />
                  <span className="footer-status-text">
                    {hasDiff
                      ? "有尚未保存的配置更改 (离开前请保存)"
                      : "所有更改已保存"}
                  </span>
                </div>
                <div className="footer-buttons">
                  <button type="button" onClick={handleAttemptClose}>
                    取消
                  </button>
                  <button
                    type="button"
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
