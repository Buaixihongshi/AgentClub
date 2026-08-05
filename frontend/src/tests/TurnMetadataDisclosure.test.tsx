import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TurnMetadataDisclosure } from "../components/TurnMetadataDisclosure";
import type { UsageRecord } from "../types";

describe("TurnMetadataDisclosure usage", () => {
  it("shows per-turn usage when turn has usage", () => {
    const usage: UsageRecord = {
      input_tokens: 24225,
      cached_input_tokens: 6528,
      output_tokens: 140,
      reasoning_output_tokens: 120,
      cache_creation_input_tokens: 0,
      total_cost_usd: 0.05,
      duration_ms: 3000,
    };
    render(<TurnMetadataDisclosure metadata={{ turn_id: "t1" }} usage={usage} />);
    const block = screen.getByTestId("turn-usage");
    expect(block).toBeTruthy();
    expect(block.textContent).toContain("24.2k");
    expect(block.textContent).toContain("6.5k");
    expect(block.textContent).toContain("$0.0500");
  });

  it("omits usage block when turn has none", () => {
    render(<TurnMetadataDisclosure metadata={{ turn_id: "t1" }} />);
    expect(screen.queryByTestId("turn-usage")).toBeNull();
  });
});
