import { useEffect, useState } from "react";
import { Wrench, X } from "lucide-react";

import {
  createMaintenanceRun,
  getMaintenanceRun,
  listMaintenanceRuns,
  listRegisteredRepositories,
  registerRepository,
  resumeMaintenanceRun,
  validateMaintenanceRun,
} from "../lib/api";
import type { MaintenanceRun, MaintenanceRunDetail, RegisteredRepository } from "../types";

export function MaintenanceButton() {
  const [open, setOpen] = useState(false);
  const [repositories, setRepositories] = useState<RegisteredRepository[]>([]);
  const [runs, setRuns] = useState<MaintenanceRun[]>([]);
  const [detail, setDetail] = useState<MaintenanceRunDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    const [nextRepositories, nextRuns] = await Promise.all([
      listRegisteredRepositories(),
      listMaintenanceRuns(),
    ]);
    setRepositories(nextRepositories);
    setRuns(nextRuns);
  }

  useEffect(() => {
    if (!open) return;
    refresh().catch((err) => setError(messageOf(err)));
  }, [open]);

  async function handleRegister(form: HTMLFormElement) {
    const data = new FormData(form);
    const lines = String(data.get("commands") ?? "")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    let validationCommands: string[][];
    try {
      validationCommands = lines.map((line) => {
        const parsed: unknown = JSON.parse(line);
        if (!Array.isArray(parsed) || parsed.some((part) => typeof part !== "string")) {
          throw new Error("命令必须是 JSON 字符串数组");
        }
        return parsed;
      });
    } catch (err) {
      setError(messageOf(err));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await registerRepository({
        name: String(data.get("name") ?? ""),
        local_path: String(data.get("local_path") ?? ""),
        github_repo: String(data.get("github_repo") ?? ""),
        validation_commands: validationCommands,
      });
      form.reset();
      await refresh();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreate(form: HTMLFormElement) {
    const data = new FormData(form);
    setBusy(true);
    setError(null);
    try {
      const created = await createMaintenanceRun({
        repository_id: String(data.get("repository_id") ?? ""),
        goal: String(data.get("goal") ?? ""),
        issue_url: String(data.get("issue_url") ?? "") || undefined,
      });
      setDetail(created);
      form.reset();
      await refresh();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  async function openRun(runId: string) {
    setBusy(true);
    setError(null);
    try {
      setDetail(await getMaintenanceRun(runId));
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  async function validateRun() {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      const next = await validateMaintenanceRun(detail.run.id);
      setDetail(next);
      await refresh();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  async function resumeRun() {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      const next = await resumeMaintenanceRun(detail.run.id);
      setDetail(next);
      await refresh();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="settings-button"
        aria-label="代码仓库维护"
        data-testid="maintenance-button"
        onClick={() => setOpen(true)}
      >
        <Wrench size={18} strokeWidth={2} aria-hidden="true" />
      </button>
      {open ? (
        <div className="maintenance-backdrop" onClick={() => setOpen(false)}>
          <section className="maintenance-modal" onClick={(event) => event.stopPropagation()}>
            <header className="maintenance-header">
              <div>
                <p className="maintenance-kicker">REPOSITORY MAINTENANCE</p>
                <h2>代码仓库维护团队</h2>
              </div>
              <button type="button" aria-label="关闭代码仓库维护" onClick={() => setOpen(false)}>
                <X size={18} />
              </button>
            </header>
            {error ? <div role="alert" className="error-banner">{error}</div> : null}
            <div className="maintenance-grid">
              <div className="maintenance-column">
                <h3>登记仓库</h3>
                <form onSubmit={(event) => { event.preventDefault(); void handleRegister(event.currentTarget); }}>
                  <input name="name" required placeholder="显示名称" />
                  <input name="local_path" required placeholder="本地仓库绝对路径" />
                  <input name="github_repo" required placeholder="owner/repository" />
                  <textarea
                    name="commands"
                    required
                    rows={3}
                    placeholder={'每行一个 JSON 参数数组，例如\n["npm","test"]'}
                  />
                  <button type="submit" disabled={busy}>登记</button>
                </form>
                <ul className="maintenance-list">
                  {repositories.map((repository) => (
                    <li key={repository.id}>
                      <strong>{repository.name}</strong>
                      <span>{repository.github_repo} · {repository.default_branch}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="maintenance-column">
                <h3>创建维护任务</h3>
                <form onSubmit={(event) => { event.preventDefault(); void handleCreate(event.currentTarget); }}>
                  <select name="repository_id" required defaultValue="">
                    <option value="" disabled>选择已登记仓库</option>
                    {repositories.map((repository) => (
                      <option key={repository.id} value={repository.id}>{repository.name}</option>
                    ))}
                  </select>
                  <textarea name="goal" required rows={3} placeholder="描述代码维护目标" />
                  <input name="issue_url" placeholder="GitHub Issue URL（可选）" />
                  <button type="submit" disabled={busy || repositories.length === 0}>创建隔离工作区</button>
                </form>
                <ul className="maintenance-list">
                  {runs.map((run) => (
                    <li key={run.id}>
                      <button type="button" onClick={() => void openRun(run.id)}>
                        <strong>{run.goal}</strong>
                        <span>{phaseLabel(run.phase)} · {run.branch_name ?? "等待分支"}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="maintenance-column maintenance-detail">
                <h3>任务详情</h3>
                {detail ? (
                  <>
                    <p><strong>{detail.run.goal}</strong></p>
                    <dl>
                      <dt>阶段</dt><dd>{phaseLabel(detail.run.phase)}</dd>
                      <dt>分支</dt><dd>{detail.run.branch_name ?? "—"}</dd>
                      <dt>工作区</dt><dd>{detail.run.workspace_path ?? "—"}</dd>
                    </dl>
                    {detail.run.last_error ? <p className="runtime-error">{detail.run.last_error}</p> : null}
                    <button type="button" disabled={busy || !detail.run.workspace_path} onClick={() => void validateRun()}>
                      运行验证契约
                    </button>
                    {detail.run.phase === "blocked" || detail.run.phase === "failed" ? (
                      <button type="button" disabled={busy} onClick={() => void resumeRun()}>
                        从检查点恢复
                      </button>
                    ) : null}
                    <ul className="maintenance-list">
                      {detail.validation_results.map((result) => (
                        <li key={result.id}>
                          <strong>{result.exit_code === 0 ? "通过" : "失败"} · {result.command.join(" ")}</strong>
                          <span>{result.duration_ms} ms</span>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : <p>选择一个维护任务查看状态。</p>}
              </div>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}

function phaseLabel(phase: MaintenanceRun["phase"]): string {
  const labels: Record<MaintenanceRun["phase"], string> = {
    queued: "排队中",
    planning: "待规划",
    implementing: "实现中",
    local_verifying: "本地验证",
    reviewing: "待审查",
    draft_pr: "草稿 PR",
    ci_waiting: "等待 CI",
    ci_repairing: "修复 CI",
    ready_for_approval: "等待合并批准",
    merging: "合并中",
    completed: "已完成",
    waiting_approval: "等待批准",
    blocked: "已阻塞",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[phase];
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败";
}
