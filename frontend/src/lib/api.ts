import type { ArtifactContent, Conversation, ConversationDetail, ConversationSummary, HealthView, HubSettingsPatch, HubSettingsView, Mission, MissionDetail, OnboardingFirstChatResponse, OnboardingView } from "../types";

const API_ROOT = "";

async function parseJson<T>(response: Response, fallbackMessage: string): Promise<T> {
  if (!response.ok) {
    let detail: string | null = null;
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) {
        detail = body.detail;
      }
    } catch {}
    throw new Error(detail || fallbackMessage);
  }
  return response.json() as Promise<T>;
}

export async function listMissions(): Promise<Mission[]> {
  const response = await fetch(`${API_ROOT}/api/missions`);
  return parseJson<Mission[]>(response, "加载任务列表失败");
}

export async function fetchMission(missionId: string): Promise<MissionDetail> {
  const response = await fetch(`${API_ROOT}/api/missions/${missionId}`);
  return parseJson<MissionDetail>(response, "加载任务详情失败");
}

export async function createMission(
  goal: string,
  targetAgentIds?: string[],
): Promise<MissionDetail> {
  const response = await fetch(`${API_ROOT}/api/missions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      goal,
      target_agent_ids: targetAgentIds && targetAgentIds.length > 0 ? targetAgentIds : undefined,
    }),
  });
  return parseJson<MissionDetail>(response, "创建任务失败");
}

export async function interruptMission(
  missionId: string,
  content: string,
  targetAgentId?: string
): Promise<MissionDetail> {
  const response = await fetch(`${API_ROOT}/api/missions/${missionId}/interruptions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, target_agent_id: targetAgentId })
  });
  return parseJson<MissionDetail>(response, "发送插话失败");
}

export async function fetchArtifactContent(
  missionId: string,
  path: string,
): Promise<ArtifactContent> {
  const response = await fetch(
    `${API_ROOT}/api/missions/${missionId}/artifacts/content?path=${encodeURIComponent(path)}`,
  );
  return parseJson<ArtifactContent>(response, "加载产物内容失败");
}

export function missionStreamUrl(missionId: string): string {
  return `${API_ROOT}/api/missions/${missionId}/stream`;
}

export async function listConversations(status: "active" | "archived" = "active"): Promise<ConversationSummary[]> {
  const response = await fetch(`${API_ROOT}/api/conversations?status=${status}`);
  return parseJson<ConversationSummary[]>(response, "加载会话列表失败");
}

export async function createConversation(title?: string): Promise<Conversation> {
  const response = await fetch(`${API_ROOT}/api/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title || undefined }),
  });
  return parseJson<Conversation>(response, "创建会话失败");
}

export async function updateConversation(
  conversationId: string,
  patch: { title?: string; status?: "active" | "archived" },
): Promise<ConversationDetail> {
  const response = await fetch(`${API_ROOT}/api/conversations/${conversationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return parseJson<ConversationDetail>(response, "更新会话详情失败");
}

export async function fetchConversation(conversationId: string): Promise<ConversationDetail> {
  const response = await fetch(`${API_ROOT}/api/conversations/${conversationId}`);
  return parseJson<ConversationDetail>(response, "加载会话详情失败");
}

export async function createConversationMission(
  conversationId: string,
  goal: string,
  targetAgentIds?: string[],
): Promise<ConversationDetail> {
  const response = await fetch(`${API_ROOT}/api/conversations/${conversationId}/missions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      goal,
      target_agent_ids: targetAgentIds && targetAgentIds.length > 0 ? targetAgentIds : undefined,
    }),
  });
  return parseJson<ConversationDetail>(response, "在会话中创建任务失败");
}

export async function getHubSettings(): Promise<HubSettingsView> {
  const response = await fetch(`${API_ROOT}/api/hub/settings`);
  return parseJson<HubSettingsView>(response, "加载 Hub 设置失败");
}

export async function updateHubSettings(patch: HubSettingsPatch): Promise<HubSettingsView> {
  const response = await fetch(`${API_ROOT}/api/hub/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return parseJson<HubSettingsView>(response, "更新 Hub 设置失败");
}

export interface AgentSummary {
  id: string;
  display_name: string;
  role: string;
  aliases: string[];
  capability_hints: string[];
}

export async function listAgents(): Promise<AgentSummary[]> {
  const response = await fetch(`${API_ROOT}/api/agents`);
  const body = await parseJson<{ agents: AgentSummary[] }>(response, "加载 agent 列表失败");
  return body.agents;
}

export async function getHealth(): Promise<HealthView> {
  const response = await fetch(`${API_ROOT}/api/health`);
  return parseJson<HealthView>(response, "AgentClub 后端尚未就绪");
}

export async function getOnboarding(): Promise<OnboardingView> {
  const response = await fetch(`${API_ROOT}/api/onboarding`);
  return parseJson<OnboardingView>(response, "加载启动引导失败");
}

export async function startOnboardingFirstChat(): Promise<OnboardingFirstChatResponse> {
  const response = await fetch(`${API_ROOT}/api/onboarding/first-chat`, {
    method: "POST",
  });
  return parseJson<OnboardingFirstChatResponse>(response, "启动首次对话失败");
}

export async function completeOnboarding(dismissedRealCliSetup = false): Promise<OnboardingView> {
  const response = await fetch(`${API_ROOT}/api/onboarding`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ completed: true, dismissed_real_cli_setup: dismissedRealCliSetup }),
  });
  return parseJson<OnboardingView>(response, "完成启动引导失败");
}

export async function verifyOnboardingProvider(providerId: string): Promise<OnboardingFirstChatResponse> {
  const response = await fetch(`${API_ROOT}/api/onboarding/providers/${providerId}/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed_real_call: true }),
  });
  return parseJson<OnboardingFirstChatResponse>(response, "真实 CLI 验证启动失败");
}

export async function activateOnboardingProvider(providerId: string): Promise<OnboardingFirstChatResponse> {
  const response = await fetch(`${API_ROOT}/api/onboarding/providers/${providerId}/activate`, {
    method: "POST",
  });
  return parseJson<OnboardingFirstChatResponse>(response, "真实 CLI 接管失败");
}
