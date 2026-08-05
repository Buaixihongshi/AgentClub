import { useCallback, useEffect, useMemo, useState } from "react";
import { MissionRoomPage } from "./components/MissionRoomPage";
import { useMissionStream } from "./hooks/useMissionStream";
import { useProviderStatus } from "./hooks/useProviderStatus";
import {
  completeOnboarding,
  activateOnboardingProvider,
  createConversation,
  createConversationMission,
  fetchConversation,
  getHealth,
  getHubSettings,
  getOnboarding,
  interruptMission,
  listAgents,
  listConversations,
  updateConversation,
  updateHubSettings,
  startOnboardingFirstChat,
  verifyOnboardingProvider,
} from "./lib/api";
import { buildAgentParticipants, buildMissionRuntimeSummary, buildTurnViews } from "./lib/missionView";
import {
  collectMentionTargetIds,
  parseMentionTarget,
  parseMentionTargets,
  setMentionOptions,
} from "./lib/mentions";
import {
  isTerminalMissionStatus,
  type ConversationDetail,
  type ConversationSummary,
  type HealthView,
  type HubSettingsPatch,
  type HubSettingsView,
  type MissionDetail,
  type OnboardingView,
  type ThreadEvent,
} from "./types";
import "./styles.css";

export default function App() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationScope, setConversationScope] = useState<"active" | "archived">("active");
  const [selectedConvId, setSelectedConvId] = useState<string | undefined>();
  const [convDetail, setConvDetail] = useState<ConversationDetail | undefined>();
  const [latestMissionDetail, setLatestMissionDetail] = useState<MissionDetail | undefined>();
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hubSettings, setHubSettings] = useState<HubSettingsView | null>(null);
  const [health, setHealth] = useState<HealthView | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [onboarding, setOnboarding] = useState<OnboardingView | null>(null);
  const [onboardingBusy, setOnboardingBusy] = useState(false);
  const [onboardingError, setOnboardingError] = useState<string | null>(null);
  const providerStatus = useProviderStatus();

  useEffect(() => {
    listConversations().then(setConversations).catch(() => {});
    getHubSettings().then(setHubSettings).catch(() => {});
    listAgents()
      .then((agents) =>
        setMentionOptions(
          agents.map((a) => ({
            id: a.id,
            label: a.display_name,
            aliases: a.aliases,
            role: a.role,
          })),
        ),
      )
      .catch(() => {});
  }, []);

  const loadHealth = useCallback(async () => {
    setHealthLoading(true);
    setHealthError(null);
    try {
      setHealth(await getHealth());
    } catch (err) {
      setHealth(null);
      setHealthError(err instanceof Error ? err.message : "后端尚未就绪");
    } finally {
      setHealthLoading(false);
    }
  }, []);

  const loadOnboarding = useCallback(async () => {
    try {
      const state = await getOnboarding();
      setOnboarding(state);
      if (state.state !== "completed" && state.conversation_id) {
        const detail = await fetchConversation(state.conversation_id);
        setSelectedConvId(state.conversation_id);
        setConvDetail(detail);
        setLatestMissionDetail(detail.latest_mission_detail ?? undefined);
      }
    } catch (err) {
      setOnboardingError(err instanceof Error ? err.message : "加载启动引导失败");
    }
  }, []);

  useEffect(() => {
    void loadHealth();
    void loadOnboarding();
  }, [loadHealth, loadOnboarding]);

  const handleSaveSettings = useCallback(async (patch: HubSettingsPatch) => {
    try {
      const updated = await updateHubSettings(patch);
      setHubSettings(updated);
      providerStatus.refresh();
    } catch {
      setError("保存设置失败");
    }
  }, [providerStatus.refresh]);

  const hasRunningMission = Boolean(
    latestMissionDetail && !isTerminalMissionStatus(latestMissionDetail.mission.status),
  );

  const handleSnapshot = useCallback((snapshot: MissionDetail) => {
    setLatestMissionDetail((prev) => {
      if (prev && snapshot.mission.id === prev.mission.id && isOlderSnapshot(snapshot, prev)) {
        return prev;
      }
      return snapshot;
    });
    setError(null);
  }, []);

  const streamState = useMissionStream(
    hasRunningMission ? latestMissionDetail?.mission.id : undefined,
    handleSnapshot,
  );

  useEffect(() => {
    if (
      latestMissionDetail &&
      isTerminalMissionStatus(latestMissionDetail.mission.status) &&
      selectedConvId
    ) {
      fetchConversation(selectedConvId).then(setConvDetail).catch(() => {});
      listConversations().then(setConversations).catch(() => {});
      if (
        onboarding?.mission_id === latestMissionDetail.mission.id ||
        onboarding?.verification_mission_id === latestMissionDetail.mission.id ||
        onboarding?.team_intro_mission_id === latestMissionDetail.mission.id
      ) {
        getOnboarding().then(setOnboarding).catch(() => {});
      }
    }
  }, [latestMissionDetail?.mission.status, latestMissionDetail?.mission.id, selectedConvId, onboarding?.mission_id, onboarding?.verification_mission_id, onboarding?.team_intro_mission_id]);

  const displayMessages = useMemo((): ThreadEvent[] => {
    const convMessages = convDetail?.messages ?? [];
    if (!latestMissionDetail) return convMessages;
    if (latestMissionDetail.mission.kind === "provider_verification") {
      return convMessages;
    }
    const snapshotMsgs = latestMissionDetail.messages ?? latestMissionDetail.events ?? [];
    const existingIds = new Set(convMessages.map((m) => m.id));
    const newMsgs = snapshotMsgs.filter((m) => !existingIds.has(m.id));
    return [...convMessages, ...newMsgs];
  }, [convDetail, latestMissionDetail]);

  const turns = useMemo(
    () => buildTurnViews(
      displayMessages,
      latestMissionDetail?.mission.kind === "provider_verification"
        ? []
        : latestMissionDetail?.a2a_invocations ?? [],
      {
        livePartial: latestMissionDetail?.mission.kind === "provider_verification" ? undefined : latestMissionDetail?.live_partial,
        turns: latestMissionDetail?.mission.kind === "provider_verification" ? undefined : latestMissionDetail?.turns,
        usageByTurn: latestMissionDetail?.mission.kind === "provider_verification" ? undefined : latestMissionDetail?.usage_summary?.by_turn,
        thoughts: latestMissionDetail?.mission.kind === "provider_verification" ? undefined : latestMissionDetail?.thoughts,
        toolEvents: latestMissionDetail?.mission.kind === "provider_verification" ? undefined : latestMissionDetail?.tool_events,
      },
    ),
    [displayMessages, latestMissionDetail?.mission.kind, latestMissionDetail?.a2a_invocations, latestMissionDetail?.live_partial, latestMissionDetail?.turns, latestMissionDetail?.usage_summary, latestMissionDetail?.thoughts, latestMissionDetail?.tool_events],
  );
  const agentParticipants = useMemo(
    () => buildAgentParticipants(turns),
    [turns],
  );
  const runtimeSummary = useMemo(
    () => buildMissionRuntimeSummary(latestMissionDetail, turns),
    [latestMissionDetail, turns],
  );

  async function handleSelectConversation(convId: string) {
    setSelectedConvId(convId);
    try {
      const detail = await fetchConversation(convId);
      setConvDetail(detail);
      if (detail.latest_mission_detail) {
        setLatestMissionDetail(detail.latest_mission_detail);
      } else {
        setLatestMissionDetail(undefined);
      }
    } catch {
      setError("加载会话失败");
    }
  }

  async function handleRenameConversation(convId: string, title: string) {
    try {
      const detail = await updateConversation(convId, { title });
      if (convId === selectedConvId) {
        setConvDetail(detail);
      }
      listConversations().then(setConversations).catch(() => {});
    } catch {
      setError("重命名失败");
    }
  }

  async function handleConversationStatus(convId: string, status: "active" | "archived") {
    try {
      await updateConversation(convId, { status });
      if (status === "archived" && convId === selectedConvId) {
        setSelectedConvId(undefined);
        setConvDetail(undefined);
        setLatestMissionDetail(undefined);
      }
      setConversations(await listConversations(conversationScope));
    } catch (err) {
      setError(err instanceof Error ? err.message : status === "archived" ? "归档失败" : "恢复失败");
    }
  }

  async function handleConversationScopeChange(scope: "active" | "archived") {
    setConversationScope(scope);
    setError(null);
    try {
      setConversations(await listConversations(scope));
      setSelectedConvId(undefined);
      setConvDetail(undefined);
      setLatestMissionDetail(undefined);
    } catch {
      setError("加载会话列表失败");
    }
  }

  async function handleNewConversation() {
    try {
      const conv = await createConversation();
      setSelectedConvId(conv.id);
      setConvDetail({
        conversation: conv,
        missions: [],
        messages: [],
        latest_mission_detail: null,
      });
      setLatestMissionDetail(undefined);
      const updated = await listConversations();
      setConversations(updated);
    } catch {
      setError("创建会话失败");
    }
  }

  async function submit() {
    const rawInput = input.trim();
    const multi = parseMentionTargets(rawInput);
    const missionTargetAgentIds = collectMentionTargetIds(rawInput);
    const single = parseMentionTarget(rawInput);
    if (!multi.content) return;
    setBusy(true);
    setError(null);
    try {
      if (latestMissionDetail && hasRunningMission) {
        // Interrupts retarget a single in-flight agent, not a discussion.
        const updated = await interruptMission(
          latestMissionDetail.mission.id,
          rawInput,
          single.targetAgentId,
        );
        setLatestMissionDetail(updated);
      } else if (selectedConvId) {
        const detail = await createConversationMission(
          selectedConvId,
          rawInput,
          missionTargetAgentIds,
        );
        setConvDetail(detail);
        if (detail.latest_mission_detail) {
          setLatestMissionDetail(detail.latest_mission_detail);
        }
        listConversations().then(setConversations).catch(() => {});
      } else {
        const conv = await createConversation();
        setSelectedConvId(conv.id);
        const detail = await createConversationMission(
          conv.id,
          rawInput,
          missionTargetAgentIds,
        );
        setConvDetail(detail);
        if (detail.latest_mission_detail) {
          setLatestMissionDetail(detail.latest_mission_detail);
        }
        listConversations().then(setConversations).catch(() => {});
      }
      setInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "请求失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleStartOnboarding() {
    setOnboardingBusy(true);
    setOnboardingError(null);
    try {
      const response = await startOnboardingFirstChat();
      setOnboarding(response.onboarding);
      setSelectedConvId(response.conversation.conversation.id);
      setConvDetail(response.conversation);
      setLatestMissionDetail(response.conversation.latest_mission_detail ?? undefined);
      listConversations().then(setConversations).catch(() => {});
    } catch (err) {
      setOnboardingError(err instanceof Error ? err.message : "启动首次对话失败");
    } finally {
      setOnboardingBusy(false);
    }
  }

  async function handleCompleteOnboarding() {
    setOnboardingBusy(true);
    setOnboardingError(null);
    try {
      setOnboarding(await completeOnboarding(false));
    } catch (err) {
      setOnboardingError(err instanceof Error ? err.message : "完成启动引导失败");
    } finally {
      setOnboardingBusy(false);
    }
  }

  async function handleSkipRealCliSetup() {
    setOnboardingBusy(true);
    setOnboardingError(null);
    try {
      setOnboarding(await completeOnboarding(true));
    } catch (err) {
      setOnboardingError(err instanceof Error ? err.message : "完成启动引导失败");
    } finally {
      setOnboardingBusy(false);
    }
  }

  async function handleUseProviderForTeam(providerId: string) {
    setOnboardingBusy(true);
    setOnboardingError(null);
    try {
      const agents = Object.fromEntries(
        ["chulan", "baobao", "wangye"].map((agentId) => [
          agentId,
          { default_provider_id: providerId, fallback_provider_ids: ["fake"] },
        ]),
      );
      const updated = await updateHubSettings({
        runtime: { use_fake_cli: false },
        providers: { [providerId]: { enabled: true } },
        agents,
      });
      setHubSettings(updated);
      setOnboarding(await completeOnboarding(false));
      providerStatus.refresh();
    } catch (err) {
      setOnboardingError(err instanceof Error ? err.message : "团队配置保存失败");
    } finally {
      setOnboardingBusy(false);
    }
  }

  async function handleVerifyProvider(providerId: string) {
    setOnboardingBusy(true);
    setOnboardingError(null);
    try {
      const response = await verifyOnboardingProvider(providerId);
      setOnboarding(response.onboarding);
      setSelectedConvId(response.conversation.conversation.id);
      setConvDetail(response.conversation);
      setLatestMissionDetail(response.conversation.latest_mission_detail ?? undefined);
      listConversations().then(setConversations).catch(() => {});
    } catch (err) {
      setOnboardingError(err instanceof Error ? err.message : "真实 CLI 验证启动失败");
    } finally {
      setOnboardingBusy(false);
    }
  }

  async function handleActivateProvider(providerId: string) {
    setOnboardingBusy(true);
    setOnboardingError(null);
    try {
      const response = await activateOnboardingProvider(providerId);
      setOnboarding(response.onboarding);
      setSelectedConvId(response.conversation.conversation.id);
      setConvDetail(response.conversation);
      setLatestMissionDetail(response.conversation.latest_mission_detail ?? undefined);
      listConversations().then(setConversations).catch(() => {});
      getHubSettings().then(setHubSettings).catch(() => {});
      providerStatus.refresh();
    } catch (err) {
      setOnboardingError(err instanceof Error ? err.message : "真实 CLI 接管失败");
    } finally {
      setOnboardingBusy(false);
    }
  }

  return (
    <MissionRoomPage
      detail={latestMissionDetail}
      turns={turns}
      agentParticipants={agentParticipants}
      runtimeSummary={runtimeSummary}
      streamState={streamState}
      providerStatus={providerStatus}
      input={input}
      busy={busy}
      error={error}
      onInputChange={setInput}
      onSubmit={submit}
      conversations={conversations}
      selectedConversationId={selectedConvId}
      onSelectConversation={handleSelectConversation}
      onNewConversation={handleNewConversation}
      onRenameConversation={handleRenameConversation}
      conversationScope={conversationScope}
      onConversationScopeChange={handleConversationScopeChange}
      onConversationStatusChange={handleConversationStatus}
      displayMessages={displayMessages}
      hubSettings={hubSettings}
      onSaveSettings={handleSaveSettings}
      onboarding={{
        health,
        healthLoading,
        healthError,
        onboarding,
        actionBusy: onboardingBusy,
        actionError: onboardingError,
        onStart: handleStartOnboarding,
        onRetryHealth: loadHealth,
        onComplete: handleCompleteOnboarding,
        onSkipRealCli: handleSkipRealCliSetup,
        onUseProviderForTeam: handleUseProviderForTeam,
        onRefreshProviders: loadOnboarding,
        onVerifyProvider: handleVerifyProvider,
        onActivateProvider: handleActivateProvider,
      }}
    />
  );
}

function isOlderSnapshot(nextDetail: MissionDetail, currentDetail?: MissionDetail): boolean {
  if (!currentDetail || nextDetail.mission.id !== currentDetail.mission.id) return false;
  const nextUpdatedAt = Date.parse(nextDetail.mission.updated_at);
  const currentUpdatedAt = Date.parse(currentDetail.mission.updated_at);
  return (
    Number.isFinite(nextUpdatedAt) &&
    Number.isFinite(currentUpdatedAt) &&
    nextUpdatedAt < currentUpdatedAt
  );
}
