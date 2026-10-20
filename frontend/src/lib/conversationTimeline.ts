import type { ConversationDetail, MissionDetail, ThreadEvent } from "../types";

/** Merge the persisted conversation with the current SSE snapshot by identity. */
export function buildConversationTimeline(
  conversation?: ConversationDetail,
  latest?: MissionDetail,
): ThreadEvent[] {
  const current = latest?.mission.kind === "provider_verification" ? undefined : latest;
  const events = [
    ...(conversation?.messages ?? []),
    ...(conversation?.artifact_events ?? []).filter((event) => event.mission_id !== current?.mission.id),
    ...(conversation?.failure_events ?? []).filter((event) => event.mission_id !== current?.mission.id),
    ...(current?.messages ?? current?.events ?? []),
    ...(current?.artifact_events ?? []),
    ...(current?.failure_events ?? []),
  ];
  const byId = new Map(events.map((event) => [event.id, event]));
  return [...byId.values()].filter((event) => event.metadata?.discussion_visibility !== 'internal').sort(
    (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at),
  );
}
