import { useState } from "react";
import type { StreamState } from "../hooks/useMissionStream";
import type { ProviderStatusState } from "../hooks/useProviderStatus";
import {
  isTerminalMissionStatus,
  type AgentParticipant,
  type ConversationSummary,
  type HubSettingsPatch,
  type HubSettingsView,
  type MissionDetail,
  type MissionRuntimeSummary,
  type RoomMode,
  type ThreadEvent,
  type TurnView,
} from "../types";
import { participantLabel } from "../lib/roundtableView";
import { ConversationList } from "./ConversationList";
import { DiscussionSetup } from "./DiscussionSetup";
import { DiscussionStatus } from "./DiscussionStatus";
import { MissionComposer } from "./MissionComposer";
import { MissionHeader } from "./MissionHeader";
import { MaintenanceButton } from "./MaintenanceButton";
import { MissionSideRail } from "./MissionSideRail";
import { OnboardingCard, type OnboardingCardProps } from "./OnboardingCard";
import { SettingsButton } from "./SettingsButton";
import { TurnTimeline } from "./TurnTimeline";

export function MissionRoomPage(props: {
  detail?: MissionDetail;
  turns: TurnView[];
  agentParticipants: AgentParticipant[];
  runtimeSummary: MissionRuntimeSummary;
  streamState: StreamState;
  providerStatus: ProviderStatusState;
  input: string;
  busy: boolean;
  error?: string | null;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  conversations: ConversationSummary[];
  selectedConversationId?: string;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onRenameConversation: (id: string, title: string) => void;
  conversationScope: "active" | "archived";
  onConversationScopeChange: (scope: "active" | "archived") => void;
  onConversationStatusChange: (id: string, status: "active" | "archived") => void;
  displayMessages: ThreadEvent[];
  hubSettings?: HubSettingsView | null;
  onSaveSettings?: (patch: HubSettingsPatch) => void;
  onboarding?: OnboardingCardProps;
  composerMode: RoomMode;
  onComposerModeChange: (mode: RoomMode) => void;
  selectedParticipantIds: string[];
  onToggleParticipant: (agentId: string) => void;
  discussionHint?: string | null;
  interruptTargetId: string | null;
  onInterruptTargetChange: (agentId: string | null) => void;
  discussionBusy: boolean;
  onRequestFinish: (summaryAgentId?: string) => void;
  onRequestStop: () => void;
  onContinueDiscussion: () => void;
}) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const showOnboarding = props.onboarding?.onboarding?.state !== "completed";
  const hasActiveMission = Boolean(
    props.detail && !isTerminalMissionStatus(props.detail.mission.status),
  );
  const discussion = props.detail?.discussion ?? null;
  const interruptTargets = (discussion?.participant_ids ?? []).map((agentId) => ({
    agentId,
    label: participantLabel(agentId),
  }));
  const submitLabel = hasActiveMission
    ? "发送插话"
    : props.composerMode === "roundtable"
      ? "开始讨论"
      : "创建任务";
  const onboardingCard = props.onboarding && showOnboarding ? (
    <OnboardingCard
      {...props.onboarding}
      onOpenTeamSettings={() => {
        props.onboarding?.onComplete();
        setSettingsOpen(true);
      }}
    />
  ) : null;
  return (
    <main className="mission-room">
      <div className="mission-header-bar">
        <MissionHeader
          runtimeSummary={props.runtimeSummary}
          streamState={props.streamState}
        />
        <MaintenanceButton />
        <SettingsButton
          view={props.hubSettings ?? null}
          providerStatus={props.providerStatus}
          onboarding={props.onboarding?.onboarding}
          open={settingsOpen}
          onOpenChange={setSettingsOpen}
          onRefreshProviders={() => {
            props.providerStatus.refresh();
            props.onboarding?.onRefreshProviders();
          }}
          onVerifyProvider={props.onboarding?.onVerifyProvider}
          onSave={props.onSaveSettings ?? (() => {})}
        />
      </div>
      {props.error ? (
        <div role="alert" className="error-banner">{props.error}</div>
      ) : null}
      <div className="workspace-layout">
        <ConversationList
          conversations={props.conversations}
          selectedId={props.selectedConversationId}
          onSelect={props.onSelectConversation}
          onNew={props.onNewConversation}
          onRename={props.onRenameConversation}
          scope={props.conversationScope}
          onScopeChange={props.onConversationScopeChange}
          onStatusChange={props.onConversationStatusChange}
        />
        <div className="conversation-layout">
          <section className="conversation-main" aria-label="任务对话">
            <TurnTimeline
              turns={props.turns}
              conversationId={props.selectedConversationId}
              emptyContent={onboardingCard}
              afterContent={props.turns.length > 0 ? onboardingCard : null}
            />
            <div className="discussion-panels">
              <DiscussionSetup
                mode={props.composerMode}
                participantIds={props.selectedParticipantIds}
                onModeChange={props.onComposerModeChange}
                onToggleParticipant={props.onToggleParticipant}
                locked={hasActiveMission}
                hint={props.discussionHint}
              />
              {discussion ? (
                <DiscussionStatus
                  discussion={discussion}
                  missionStatus={props.detail?.mission.status ?? "pending"}
                  busy={props.discussionBusy}
                  onRequestFinish={props.onRequestFinish}
                  onRequestStop={props.onRequestStop}
                  onContinue={props.onContinueDiscussion}
                />
              ) : null}
              <MissionComposer
                value={props.input}
                busy={props.busy}
                hasActiveMission={hasActiveMission}
                disabled={props.conversationScope === "archived"}
                submitLabel={submitLabel}
                submitDisabled={
                  !hasActiveMission &&
                  props.composerMode === "roundtable" &&
                  props.selectedParticipantIds.length < 2
                }
                mentionsEnabled={props.composerMode === "task"}
                interruptTargets={hasActiveMission ? interruptTargets : []}
                interruptTargetId={props.interruptTargetId}
                onInterruptTargetChange={props.onInterruptTargetChange}
                onChange={props.onInputChange}
                onSubmit={props.onSubmit}
              />
            </div>
          </section>
          <MissionSideRail
            detail={props.detail}
            providerStatus={props.providerStatus}
            onboarding={props.onboarding?.onboarding}
          />
        </div>
      </div>
    </main>
  );
}
