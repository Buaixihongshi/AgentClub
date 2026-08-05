import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "../components/StatusBadge";
import type { TurnView } from "../types";

function makeStatusTurn(content: string): TurnView {
  return {
    id: "s1",
    turnIndex: 0,
    role: "system",
    eventType: "status",
    content,
    capability: "unknown",
    providerLabel: "",
    createdAt: "2026-04-25T00:00:00Z",
    metadata: {},
    isStatusEvent: true,
  };
}

describe("StatusBadge", () => {
  it("renders content as centered inline text", () => {
    const { container } = render(
      <StatusBadge turn={makeStatusTurn("Mission started")} />,
    );
    expect(screen.getByText("Mission started")).toBeInTheDocument();
    expect(container.querySelector(".status-badge")).toBeTruthy();
  });

  it("has an accessible label", () => {
    render(<StatusBadge turn={makeStatusTurn("running")} />);
    expect(screen.getByLabelText("状态：running")).toBeInTheDocument();
  });

  it("renders a dot element", () => {
    const { container } = render(
      <StatusBadge turn={makeStatusTurn("completed")} />,
    );
    expect(container.querySelector(".status-badge-dot")).toBeTruthy();
  });
});
