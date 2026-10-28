import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MissionHeader } from "../components/MissionHeader";
import type { MissionRuntimeSummary } from "../types";

const summary = (statusLabel: string): MissionRuntimeSummary => ({
  title: "Unity", statusLabel, complexityLabel: "unknown", agentTurnCount: 3, artifactCount: 0,
} as MissionRuntimeSummary);

describe("MissionHeader", () => {
  it("hides routine status, stream state and turn count", () => {
    render(<MissionHeader runtimeSummary={summary("已完成")} streamState="idle" />);
    expect(screen.getByRole("heading", { name: "Unity" })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText(/实时流/)).not.toBeInTheDocument();
    expect(screen.queryByText(/轮对话/)).not.toBeInTheDocument();
  });

  it("surfaces states that need the user's attention", () => {
    render(<MissionHeader runtimeSummary={summary("等待你的决定")} streamState="reconnecting" />);
    expect(screen.getByRole("status")).toHaveTextContent("等待你的决定");
    expect(screen.getByRole("status")).toHaveTextContent("实时流：重连中");
  });
});
