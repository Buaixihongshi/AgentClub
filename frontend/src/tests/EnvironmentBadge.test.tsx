import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EnvironmentBadge } from "../components/EnvironmentBadge";
import type { RuntimeIdentity, SourceIdentity } from "../types";

const frontend: SourceIdentity = {
  environment: "test", git_sha: "abcdef123456", branch: "main", dirty: false,
  source_digest: "same-source", checkout_id: "same-checkout", captured_at: "2026-10-22T10:00:00Z",
};
const runtime: RuntimeIdentity = {
  started: { ...frontend, pid: 123, python: "3.12.12", checkout: "/test",
    database: "/test/data.sqlite3", config: "/test/config", artifacts: "/test/artifacts", use_fake_cli: true },
  current: frontend, source_changed: false,
};

describe("environment identity", () => {
  it("shows a matching version and labels dirty startup snapshots", () => {
    render(<EnvironmentBadge frontend={{ ...frontend, dirty: true }} runtime={runtime} />);
    expect(screen.getByText("启动版本一致")).toBeInTheDocument();
    expect(screen.getByText(/前端 abcdef1\*/)).toBeInTheDocument();
    expect(screen.getByText("TEST")).toBeInTheDocument();
  });

  it("does not claim an old backend is the current frontend version", () => {
    render(<EnvironmentBadge frontend={frontend} />);
    expect(screen.getByText("版本信息不完整")).toBeInTheDocument();
    expect(screen.getByText(/后端 未知/)).toBeInTheDocument();
    expect(screen.queryByText("启动版本一致")).not.toBeInTheDocument();
  });

  it.each([
    { ...runtime, started: { ...runtime.started, git_sha: "different-sha" } },
    { ...runtime, started: { ...runtime.started, source_digest: "different-dirty-content" } },
    { ...runtime, started: { ...runtime.started, checkout_id: "other-checkout" } },
    { ...runtime, source_changed: true },
  ])("flags different sources, checkouts and changes after initialization", (backend) => {
    render(<EnvironmentBadge frontend={frontend} runtime={backend} />);
    expect(screen.getByText("环境需核对")).toBeInTheDocument();
    expect(screen.getAllByRole("alert", { hidden: true }).length).toBeGreaterThan(0);
  });
});
