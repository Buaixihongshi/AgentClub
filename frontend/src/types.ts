export type MissionStatus =
  | "queued"
  | "pending"
  | "running"
  | "blocked"
  | "completed"
  | "completed_with_warnings"
  | "failed";

export const TERMINAL_MISSION_STATUSES = [
  "blocked",
  "completed",
  "completed_with_warnings",
  "failed",
] as const;

export function isTerminalMissionStatus(status: string): boolean {
  return TERMINAL_MISSION_STATUSES.includes(
    status as (typeof TERMINAL_MISSION_STATUSES)[number],
  );
}

export type EventRole = "user" | "agent" | "assistant" | "system";
export type Capability = "planning" | "implementation" | "review" | "coordination" | "unknown";

export interface Mission {
  id: string;
  title?: string;
  goal: string;
  kind?: "task" | "provider_verification";
  status: MissionStatus;
  complexity?: string;
  current_capability?: Capability;
  current_speaker_id?: string | null;
  summary?: string;
  blocked_reason?: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface ThreadEventMetadata {
  capability?: Capability;
  provider_id?: string;
  provider_label?: string;
  persona_id?: string;
  persona_label?: string;
  turn_id?: string;
  kind?: string;
  tags?: string[];
  duration_ms?: number;
  tool_calls?: Array<{ tool_name: string; tool_input?: Record<string, unknown> }>;
  handoff_to?: string;
  trace_blocks?: Array<{ type: string; title: string; content: string }>;
  artifact_id?: string;
}

export interface ThreadEvent {
  id: number | string;
  mission_id: string;
  event_type: string;
  role: EventRole;
  content: string;
  agent_id?: string | null;
  character_id?: string | null;
  character_name?: string | null;
  provider_id?: string | null;
  turn_id?: string | null;
  metadata?: ThreadEventMetadata;
  created_at: string;
}

export interface Artifact {
  id: string;
  mission_id: string;
  title: string;
  kind?: string;
  path?: string;
  file_path?: string;
  preview?: string;
  created_at: string;
  agent_id?: string | null;
  turn_id?: string | null;
}

export interface ArtifactContent {
  path: string;
  content: string;
  kind: string;
  total_chars: number;
  truncated: boolean;
}

export interface TurnAttempt {
  attempt: number;
  provider_id: string;
  event_type?: string;
  status?: "started" | "completed" | "blocked" | "failed";
  duration_ms?: number;
  error?: string;
  summary?: string;
}

export interface TurnRecord {
  turn_id: string;
  agent_id: string;
  provider_id: string;
  status: "started" | "completed" | "blocked" | "failed";
  attempts: TurnAttempt[];
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  message_ids: string[];
}

export interface ToolEventRecord {
  id: number | string;
  mission_id: string;
  event_type: "tool.call" | "tool.result" | "tool.error";
  tool_name: string;
  agent_id: string;
  turn_id: string;
  provider_id?: string | null;
  tool_input?: Record<string, unknown>;
  artifact_id?: string | null;
  summary?: string;
  error?: string;
  created_at: string;
}

export interface InterruptRecord {
  event_id: number | string;
  content: string;
  target_agent_id?: string | null;
  status: "pending" | "handled";
  created_at: string;
  handled_at?: string | null;
  handled_by_agent_id?: string | null;
  turn_id?: string | null;
}

export interface A2AInvocationRecord {
  event_id: number | string;
  sender_agent_id: string;
  sender_turn_id: string;
  target_agent_id: string;
  /** The turn_id assigned to the invoked agent's turn. Set by the runner. */
  target_turn_id?: string | null;
  purpose: string;
  mode: "fire_and_forget" | "wait_reply";
  queued_position?: number | null;
  created_at: string;
  reply_text?: string | null;
  reply_failed?: boolean;
}

export interface UsageRecord {
  input_tokens: number;
  cached_input_tokens: number;
  cache_creation_input_tokens: number;
  output_tokens: number;
  reasoning_output_tokens: number;
  total_cost_usd: number | null;
  duration_ms: number;
}

export interface UsageSummary {
  mission_total: UsageRecord | null;
  by_agent: (UsageRecord & { agent_id: string })[];
  by_provider: (UsageRecord & { provider_id: string })[];
  by_turn: (UsageRecord & { turn_id: string; agent_id: string; provider_id: string })[];
}

export interface ThoughtEntry {
  content: string;
  kind?: string;
  tags?: string[];
  createdAt: string;
}

export interface TurnToolCall {
  toolName: string;
  summary?: string;
  toolInput?: Record<string, unknown>;
  createdAt: string;
  eventType: "tool.call" | "tool.result" | "tool.error";
}

export interface MissionThoughtRecord {
  id: number | string;
  mission_id: string;
  agent_id?: string | null;
  turn_id: string;
  provider_id?: string | null;
  content: string;
  kind?: string;
  tags?: string[];
  created_at: string;
}

export interface MissionDetail {
  mission: Mission;
  events?: ThreadEvent[];
  messages?: ThreadEvent[];
  artifacts?: Artifact[];
  turns?: TurnRecord[];
  tool_events?: ToolEventRecord[];
  interrupts?: InterruptRecord[];
  a2a_invocations?: A2AInvocationRecord[];
  usage_summary?: UsageSummary;
  live_partial?: Record<string, string>;
  thoughts?: MissionThoughtRecord[];
}

export interface TurnView {
  id: string;
  turnIndex: number;
  role: EventRole;
  eventType: string;
  content: string;
  capability: Capability;
  providerLabel: string;
  personaLabel?: string;
  agentId?: string | null;
  createdAt: string;
  metadata: ThreadEventMetadata;
  isStatusEvent: boolean;
  a2aInvocation?: A2AInvocationRecord;
  partialText?: string;
  usage?: UsageRecord;
  thoughts?: ThoughtEntry[];
  toolCalls?: TurnToolCall[];
}

export interface AgentParticipant {
  agentId: string;
  personaLabel: string;
  capability: Capability;
  providerLabel: string;
  turnIndex: number;
  participated: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
}

export interface ConversationSummary extends Conversation {
  latest_mission_id?: string | null;
  latest_message_preview?: string | null;
  mission_count: number;
}

export interface ConversationDetail {
  conversation: Conversation;
  missions: Mission[];
  messages: ThreadEvent[];
  latest_mission_detail?: MissionDetail | null;
}

export interface MissionRuntimeSummary {
  title: string;
  statusLabel: string;
  complexityLabel: string;
  agentTurnCount: number;
  artifactCount: number;
  updatedAt?: string;
}

export interface ProviderLikeStatus {
  id: string;
  label: string;
  enabled: boolean;
  available: boolean;
  version?: string | null;
  error?: string | null;
}

export type SettingSource = "user" | "env" | "default" | "committed";

export interface SettingField<T> {
  value: T;
  source: SettingSource;
}

export interface HubSettingsProviderView {
  id: string;
  label: string;
  enabled: SettingField<boolean>;
}

export interface HubSettingsRuntimeView {
  use_fake_cli: SettingField<boolean>;
  max_turns: SettingField<number>;
  max_a2a_invocations: SettingField<number>;
  cli_timeout: SettingField<number>;
}

export interface HubSettingsAgentView {
  id: string;
  display_name: string;
  default_provider_id: SettingField<string>;
  fallback_provider_ids: SettingField<string[]>;
  base_default_provider_id: string;
  base_fallback_provider_ids: string[];
}

export interface HubSettingsView {
  runtime: HubSettingsRuntimeView;
  providers: HubSettingsProviderView[];
  agents: HubSettingsAgentView[];
}

export interface ProviderOverridePatch {
  enabled?: boolean;
}

export interface RuntimePatch {
  use_fake_cli?: boolean;
  max_turns?: number;
  max_a2a_invocations?: number;
  cli_timeout?: number;
}

export interface AgentOverridePatch {
  default_provider_id?: string;
  fallback_provider_ids?: string[];
  clear?: boolean;
}

export interface HubSettingsPatch {
  providers?: Record<string, ProviderOverridePatch>;
  runtime?: RuntimePatch;
  agents?: Record<string, AgentOverridePatch>;
}

export interface HealthView {
  status: "ok";
  version: string;
  database: "ready";
  config: "ready";
  fake_provider: "ready";
}

export type OnboardingState =
  | "ready_for_first_chat"
  | "first_chat_running"
  | "first_chat_failed"
  | "first_chat_succeeded"
  | "provider_verifying"
  | "provider_verification_failed"
  | "provider_verified"
  | "team_intro_running"
  | "team_intro_failed"
  | "team_intro_completed"
  | "completed";

export interface OnboardingProviderStatus {
  id: string;
  label: string;
  available: boolean;
  version?: string | null;
  path?: string | null;
  error?: string | null;
  verified: boolean;
  verification_error?: string | null;
  activated: boolean;
  install_command?: string | null;
  install_url?: string | null;
  auth_command?: string | null;
}

export interface OnboardingView {
  state: OnboardingState;
  guide_agent_id: string;
  conversation_id?: string | null;
  mission_id?: string | null;
  verification_provider_id?: string | null;
  verification_mission_id?: string | null;
  activated_provider_id?: string | null;
  team_intro_mission_id?: string | null;
  first_reply_completed_at?: string | null;
  team_intro_completed_at?: string | null;
  completed_at?: string | null;
  verified_provider_ids: string[];
  providers: OnboardingProviderStatus[];
  recommended_provider_id?: string | null;
  verification_error?: string | null;
  dismissed_real_cli_setup: boolean;
}

export interface OnboardingFirstChatResponse {
  onboarding: OnboardingView;
  conversation: ConversationDetail;
}
