import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TurnTimeline } from "../components/TurnTimeline";
import type { TurnView } from "../types";

describe("TurnTimeline", () => {

function agentTurn(overrides: Partial<TurnView>): TurnView {
  return {
    id: "turn-view",
    turnIndex: 1,
    role: "agent",
    eventType: "message",
    content: "我来处理",
    capability: "implementation",
    providerLabel: "Codex CLI",
    personaLabel: "冯宝宝",
    agentId: "baobao",
    createdAt: "2026-05-11T00:00:00Z",
    metadata: {},
    isStatusEvent: false,
    ...overrides,
  };
}

it("renders an A2A arrow before an invoked agent turn", () => {
  render(
    <TurnTimeline
      turns={[
        agentTurn({
          a2aInvocation: {
            event_id: 1,
            sender_agent_id: "chulan",
            sender_turn_id: "turn_1",
            target_agent_id: "baobao",
            purpose: "核对异闻物档案",
            mode: "fire_and_forget",
            queued_position: 1,
            created_at: "2026-05-11T00:00:00Z",
          },
        }),
      ]}
    />,
  );

  expect(screen.getByText("张楚岚 → 冯宝宝")).toBeInTheDocument();
  expect(screen.getByText("核对异闻物档案")).toBeInTheDocument();
});

it("renders without A2A arrow when no invocation", () => {
  render(<TurnTimeline turns={[agentTurn({})]} />);

  expect(screen.queryByText(/→/)).not.toBeInTheDocument();
});

it("keeps V2 raw speech in a closed disclosure instead of duplicating the structured board", () => {
  render(<TurnTimeline turns={[
    agentTurn({ id: "old", missionId: "previous", content: "以前的讨论" }),
    agentTurn({ id: "new", missionId: "current", content: "本场原文" }),
  ]} collapsedAgentMissionId="current" />);
  expect(screen.getByText("以前的讨论")).toBeVisible();
  expect(screen.getByText("本场原文")).not.toBeVisible();
  expect(screen.getByText("查看本场原始发言（1 条）")).toBeVisible();
});
});
