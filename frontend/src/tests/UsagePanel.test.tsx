import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { UsagePanel } from "../components/UsagePanel";
import type { UsageSummary } from "../types";

const fullSummary: UsageSummary = {
  mission_total: {
    input_tokens: 24225,
    cached_input_tokens: 6528,
    output_tokens: 140,
    reasoning_output_tokens: 120,
    cache_creation_input_tokens: 0,
    total_cost_usd: 0.0123,
    duration_ms: 5000,
  },
  by_agent: [
    {
      agent_id: "chulan",
      input_tokens: 14225,
      cached_input_tokens: 3000,
      output_tokens: 80,
      reasoning_output_tokens: 60,
      cache_creation_input_tokens: 0,
      total_cost_usd: 0.007,
      duration_ms: 2500,
    },
    {
      agent_id: "baobao",
      input_tokens: 10000,
      cached_input_tokens: 3528,
      output_tokens: 60,
      reasoning_output_tokens: 60,
      cache_creation_input_tokens: 0,
      total_cost_usd: 0.0053,
      duration_ms: 2500,
    },
  ],
  by_provider: [],
  by_turn: [],
};

describe("UsagePanel", () => {
  it("renders mission total tokens with four columns", () => {
    render(<UsagePanel usageSummary={fullSummary} />);
    expect(screen.getAllByText("input").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("cached").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("output").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("reasoning").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("24.2k")).toBeTruthy();
    expect(screen.getByText("6.5k")).toBeTruthy();
  });

  it("shows USD line only when total_cost_usd is present", () => {
    render(<UsagePanel usageSummary={fullSummary} />);
    expect(screen.getByText("$0.0123")).toBeTruthy();
  });

  it("hides USD when total_cost_usd is null", () => {
    const noUsd: UsageSummary = {
      ...fullSummary,
      mission_total: { ...fullSummary.mission_total!, total_cost_usd: null },
    };
    render(<UsagePanel usageSummary={noUsd} />);
    expect(screen.queryByText(/\$/)).toBeNull();
  });

  it("renders per-agent breakdown table", () => {
    render(<UsagePanel usageSummary={fullSummary} />);
    expect(screen.getByText("张楚岚")).toBeTruthy();
    expect(screen.getByText("冯宝宝")).toBeTruthy();
    expect(screen.getByText("按 Agent")).toBeTruthy();
  });

  it("renders empty-state copy when usage_summary is null", () => {
    render(<UsagePanel usageSummary={undefined} />);
    expect(screen.getByText(/仅真实 CLI 提供/)).toBeTruthy();
  });

  it("renders empty-state when mission_total is null", () => {
    const empty: UsageSummary = {
      mission_total: null,
      by_agent: [],
      by_provider: [],
      by_turn: [],
    };
    render(<UsagePanel usageSummary={empty} />);
    expect(screen.getByText(/仅真实 CLI 提供/)).toBeTruthy();
  });
});
