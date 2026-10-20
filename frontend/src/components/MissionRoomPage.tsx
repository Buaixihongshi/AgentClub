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
  type ThreadEvent,
  type TurnView,
} from "../types";
import { ConversationList } from "./ConversationList";
import { DiscussionSkillStatus } from "./DiscussionSkillStatus";
import { MissionComposer } from "./MissionComposer";
import { MissionHeader } from "./MissionHeader";
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
  discussionBusy: boolean;
  onRequestFinish: () => void;
  onDiscussionControl?: (action: import("../types").DiscussionControlAction, fields?: import("../types").DiscussionControlFields) => void;
  onRequestStop: () => void;
  onContinueDiscussion: () => void;
}) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const showOnboarding = props.onboarding?.onboarding?.state !== "completed";
  const hasActiveMission = Boolean(
    props.detail && (!isTerminalMissionStatus(props.detail.mission.status) ||
      (props.detail.discussion?.protocol === 'deliberation_v2' && props.detail.discussion.phase !== 'ended')),
  );
  const discussion = props.detail?.discussion ?? null;
  const submitLabel = hasActiveMission
    ? "发送插话"
    : "创建任务";
  const onboardingCard = props.onboarding && showOnboarding && !(discussion?.protocol === 'deliberation_v2' && props.turns.length > 0) ? (
    <OnboardingCard
      {...props.onboarding}
      onOpenTeamSettings={() => {
        props.onboarding?.onComplete();
        setSettingsOpen(true);
      }}
    />
  ) : null;
  const timeline = <TurnTimeline turns={props.turns} conversationId={props.selectedConversationId}
    emptyContent={onboardingCard} afterContent={props.turns.length > 0 ? onboardingCard : null} />;
  const discussionPanel = discussion ? <DiscussionSkillStatus key={props.detail?.mission.id} discussion={discussion}
    missionStatus={props.detail?.mission.status ?? 'pending'} busy={props.discussionBusy}
    onRequestFinish={props.onRequestFinish} onControl={props.onDiscussionControl}
    onRequestStop={props.onRequestStop} onContinue={props.onContinueDiscussion} /> : null;
  return (
    <main className="mission-room">
      <div className="mission-header-bar">
        <MissionHeader
          runtimeSummary={props.runtimeSummary}
          streamState={props.streamState}
        />
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
            {timeline}
            <div className="discussion-panels composer-layout-compact">
              {discussionPanel}
              <MissionComposer
                value={props.input}
                busy={props.busy}
                hasActiveMission={hasActiveMission}
                disabled={props.conversationScope === "archived"}
                submitLabel={submitLabel}
                mentionsEnabled={!(hasActiveMission && discussion)}
                placeholder="输入消息，/discuss 发起团队讨论，@ 选择 Agent"
                onChange={props.onInputChange}
                onSubmit={props.onSubmit}
              />
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
