import type { A2AInvocationRecord, MissionDetail, ToolEventRecord, TurnRecord } from "../types";

export interface TraceTool {
  id: string;
  name: string;
  input?: Record<string, unknown>;
  outcome?: ToolEventRecord;
}

export interface TraceTurn {
  id: string;
  index: number;
  agentId: string;
  providerId: string;
  status: TurnRecord["status"];
  issue?: string;
  tools: TraceTool[];
}

export interface TraceView {
  turns: TraceTurn[];
  invocations: A2AInvocationRecord[];
  callCount: number;
}

/** Project the append-only tool timeline into calls without counting results twice. */
export function buildTraceView(detail?: MissionDetail): TraceView {
  const turns = (detail?.turns ?? []).map((turn, index): TraceTurn => {
    const issue = [...turn.attempts].reverse().find((attempt) => attempt.error)?.error;
    return {
      id: turn.turn_id,
      index: index + 1,
      agentId: turn.agent_id,
      providerId: turn.provider_id,
      status: turn.status,
      issue: turn.status === "blocked" || turn.status === "failed" ? issue : undefined,
      tools: [],
    };
  });
  const byTurn = new Map(turns.map((turn) => [turn.id, turn]));
  const pending = new Map<string, TraceTool[]>();
  const events = [...(detail?.tool_events ?? [])].sort((a, b) => {
    const aId = Number(a.id);
    const bId = Number(b.id);
    return Number.isFinite(aId) && Number.isFinite(bId)
      ? aId - bId
      : a.created_at.localeCompare(b.created_at);
  });

  for (const event of events) {
    let turn = byTurn.get(event.turn_id);
    if (!turn) {
      turn = {
        id: event.turn_id,
        index: turns.length + 1,
        agentId: event.agent_id,
        providerId: event.provider_id ?? "",
        status: "started",
        tools: [],
      };
      turns.push(turn);
      byTurn.set(turn.id, turn);
    }
    const key = `${event.turn_id}\u0000${event.tool_name}`;
    if (event.event_type === "tool.call") {
      const tool: TraceTool = {
        id: String(event.id),
        name: event.tool_name,
        input: event.tool_input,
      };
      turn.tools.push(tool);
      const queue = pending.get(key) ?? [];
      queue.push(tool);
      pending.set(key, queue);
    } else {
      const tool = pending.get(key)?.shift();
      if (tool) tool.outcome = event;
      else if (event.event_type === "tool.error") {
        // Keep an unmatched failure visible without inventing a tool.call.
        turn.tools.push({ id: `error:${event.id}`, name: event.tool_name, outcome: event });
      }
    }
  }

  return {
    turns,
    invocations: detail?.a2a_invocations ?? [],
    callCount: events.filter((event) => event.event_type === "tool.call").length,
  };
}
