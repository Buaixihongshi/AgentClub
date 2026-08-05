import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentStrip } from "../components/AgentStrip";
import type { AgentParticipant } from "../types";

const participants: AgentParticipant[] = [
  {
    agentId: "chulan",
    personaLabel: "张楚岚",
    capability: "planning",
    providerLabel: "Gemini CLI",
    turnIndex: 1,
    participated: true,
  },
  {
    agentId: "baobao",
    personaLabel: "冯宝宝",
    capability: "implementation",
    providerLabel: "Codex CLI",
    turnIndex: 2,
    participated: true,
  },
  {
    agentId: "wangye",
    personaLabel: "王也",
    capability: "review",
    providerLabel: "",
    turnIndex: 0,
    participated: false,
  },
];

describe("AgentStrip", () => {
  it("renders compact participant identity and provider", () => {
    render(<AgentStrip participants={participants} />);
    const strip = screen.getByRole("region", { name: "Agent 参与状态" });
    expect(within(strip).getByText("张楚岚（Gemini）")).toBeInTheDocument();
    expect(within(strip).getByText("冯宝宝（Codex）")).toBeInTheDocument();
    expect(within(strip).queryByText("规划 · via Gemini CLI")).not.toBeInTheDocument();
    expect(within(strip).queryByText("执行 · via Codex CLI")).not.toBeInTheDocument();
  });

  it("marks agents that have not participated yet", () => {
    render(<AgentStrip participants={participants} />);
    const wangye = screen.getByText("王也").closest("article");
    expect(wangye).toHaveClass("agent-strip-card-inactive");
    expect(within(wangye as HTMLElement).getByText("未参与")).toBeInTheDocument();
  });
});
