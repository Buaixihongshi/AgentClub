import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MaintenanceButton } from "../components/MaintenanceButton";
import type { MaintenanceRunDetail, RegisteredRepository } from "../types";

const api = vi.hoisted(() => ({
  createMaintenanceRun: vi.fn(),
  getMaintenanceRun: vi.fn(),
  listMaintenanceRuns: vi.fn(),
  listRegisteredRepositories: vi.fn(),
  registerRepository: vi.fn(),
  resumeMaintenanceRun: vi.fn(),
  validateMaintenanceRun: vi.fn(),
}));

vi.mock("../lib/api", () => api);

const repository: RegisteredRepository = {
  id: "rr_fixture",
  name: "AgentClub",
  local_path: "/tmp/AgentClub",
  github_repo: "owner/AgentClub",
  remote_name: "origin",
  default_branch: "main",
  validation_commands: [["pytest", "-q"]],
  forbidden_paths: [".env"],
  max_changed_files: 30,
  max_repair_rounds: 3,
  enabled: true,
};

const detail: MaintenanceRunDetail = {
  run: {
    id: "mr_fixture",
    repository_id: repository.id,
    goal: "修复失败测试",
    phase: "planning",
    branch_name: "codex/maintenance-fixture",
    workspace_path: "/tmp/worktrees/mr_fixture",
    repair_round: 0,
    created_at: "2026-10-14T00:00:00Z",
    updated_at: "2026-10-14T00:00:00Z",
  },
  repository,
  validation_results: [],
  approvals: [],
  feedback: [],
  events: [],
};

describe("MaintenanceButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listRegisteredRepositories.mockResolvedValue([repository]);
    api.listMaintenanceRuns.mockResolvedValue([]);
  });

  it("loads registered repositories when the maintenance room opens", async () => {
    render(<MaintenanceButton />);

    await userEvent.click(screen.getByTestId("maintenance-button"));

    expect(await screen.findByRole("heading", { name: "代码仓库维护团队" })).toBeInTheDocument();
    expect(await screen.findByText("owner/AgentClub · main")).toBeInTheDocument();
    expect(api.listRegisteredRepositories).toHaveBeenCalledOnce();
    expect(api.listMaintenanceRuns).toHaveBeenCalledOnce();
  });

  it("creates an isolated maintenance run for the selected repository", async () => {
    api.createMaintenanceRun.mockResolvedValue(detail);
    render(<MaintenanceButton />);
    await userEvent.click(screen.getByTestId("maintenance-button"));
    await screen.findByText("owner/AgentClub · main");

    await userEvent.selectOptions(screen.getByRole("combobox"), repository.id);
    await userEvent.type(screen.getByPlaceholderText("描述代码维护目标"), detail.run.goal);
    await userEvent.click(screen.getByRole("button", { name: "创建隔离工作区" }));

    await waitFor(() => {
      expect(api.createMaintenanceRun).toHaveBeenCalledWith({
        repository_id: repository.id,
        goal: detail.run.goal,
        issue_url: undefined,
      });
    });
    expect(await screen.findByText(detail.run.branch_name!)).toBeInTheDocument();
    expect(screen.getByText(detail.run.workspace_path!)).toBeInTheDocument();
  });
});
