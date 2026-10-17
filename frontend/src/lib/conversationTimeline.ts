import type { ConversationDetail, MissionDetail, ThreadEvent } from "../types";

/** Merge the persisted conversation with the current SSE snapshot by identity. */
export function buildConversationTimeline(
  conversation?: ConversationDetail,
  latest?: MissionDetail,
): ThreadEvent[] {
  const current = latest?.mission.kind === "provider_verification" ? undefined : latest;
  const events = [
    ...(conversation?.messages ?? []),
    ...(conversation?.failure_events ?? []).filter((event) => event.mission_id !== current?.mission.id),
    ...(current?.messages ?? current?.events ?? []),
    ...(current?.failure_events ?? []),
  ];
  const byId = new Map(events.map((event) => [event.id, event]));
  return [...byId.values()].sort(
    (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at),
  );
}
