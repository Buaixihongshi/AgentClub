# PR 1：环境盘点与工作目录整理清单

盘点日期：2026-10-22（北京时间）。这是一份历史快照，不是持续更新的运行真相；
实时状态以 `./scripts/env.sh status` 为准。未执行删除、prune、reset、切换分支或重启旧服务。

## 服务与独立 clone

| 对象 | 观测 | 处置建议 |
| --- | --- | --- |
| 日常入口 8000/5173 | 健康；进程从主工作目录启动，后端无 reload；无启动身份 | 保持现有服务；磁盘为 `7f92ffd`，但不能据此认定内存版本 |
| 测试入口 8200/5175 | 健康；后端进程启动于 10 月 16 日；无启动身份 | 后续经授权更新验收环境；不能作为当前 PR 的验收证据 |
| development clone | `324031d`，`codex/chore-environment-gates`，干净；8100/5174 未运行 | 先核对历史用途，再决定未来承担开发还是验收职责 |
| test clone | 同为 `324031d`，存在未提交修改 | 保留 `DiscussionSetup.tsx` 与 `styles.css` 修改，核对归属后单独收口 |

旧服务不能在不重启的情况下补出可信的启动 SHA。PR 1 对旧服务如实显示未知，
不会把磁盘 HEAD 或旧 `checkout-sha` 填成运行版本。

## Worktree 快照

原有 22 个登记，PR 1 新增独立工作目录后共 23 个。其中 1 个失效登记、5 个 detached。
表中路径仅保留便于辨认的目录尾部；完整本机路径留在本地报告。

| 目录尾部 | 分支或 HEAD | 当时状态 |
| --- | --- | --- |
| `yiren-hub`（主工作目录） | `codex/feat-custom-api-and-team-models` / `7f92ffd` | 干净；日常服务使用中，保留 |
| `environment-identity/yiren-hub` | `codex/chore-environment-identity` / 基于 `f25f292` | PR 1 开发目录，保留 |
| `agentclub-discussion-star-pr.*` | `codex/docs-discussion-v2-incident-star` | 干净，待核对 PR 与提交去向 |
| `agentclub-inline-picker` | 失效登记 | Git 标记 prunable；仅列为元数据整理候选 |
| `yiren-hub-compact-pr` | `codex/compact-chat-composer` | 有未提交修改，保留 |
| `yiren-pr-turn-failure.*` | `codex/fix-turn-failure-activity` | 干净，待核对 |
| `3674/yiren-hub` | detached / `c7c7ef6` | 有未提交修改，保留 |
| `4773/yiren-hub` | `codex/fix-a2a-handoff-stalls` / `c7c7ef6` | 有未提交修改，保留 |
| `58ed/yiren-hub` | detached / `c7c7ef6` | 干净，待核对 |
| `6cc4/yiren-hub` | detached / `c7c7ef6` | 有未提交修改，保留 |
| `a35b/yiren-hub` | detached / `c7c7ef6` | 干净，待核对 |
| `agentclub-a2a-stall-fix/yiren-hub` | `codex/feat-repository-maintainer-mvp` | 干净，待核对 |
| `agentclub-personalities/yiren-hub` | `codex/feat-personality-driven-agents` | 干净，待核对 |
| `agentclub-roundtable-backend/yiren-hub` | `codex/feat-roundtable-backend` | 干净，待核对 |
| `agentclub-roundtable-context/yiren-hub` | `codex/feat-roundtable-context` | 干净，待核对 |
| `agentclub-roundtable-plan/yiren-hub` | `codex/docs-roundtable-plan` | 干净，待核对 |
| `agentclub-roundtable-ui/yiren-hub` | `codex/feat-roundtable-ui` | 干净，待核对 |
| `artifact-on-demand/yiren-hub` | `codex/fix-artifact-on-demand` | 干净，待核对 |
| `d3fb/yiren-hub` | detached / `c7c7ef6` | 干净，待核对 |
| `previous-fixes-pr/yiren-hub` | `codex/fix-cli-incident-and-chat-history` | 干净，待核对 |
| `repository-maintainer-phase2/yiren-hub` | `codex/repository-maintainer-phase2` | 有未提交修改，保留 |
| `trace-dialog-pr/yiren-hub` | `codex/trace-dialog` | 干净，待核对 |
| `antigravity-no-gemini-cutover` | 本地 `main` / `c7c7ef6` | 与当前远端主线明显分叉；保留并调查来源，不自动 reset |

干净不等于可删除。远端分支消失或 Git ancestry 检查，也不能独自证明 squash
合并后的代码与本地独有提交已经妥善保存。

## 后续整理顺序（本 PR 不执行）

1. 先确认运行进程及活跃任务占用；日常服务所在目录不动。
2. 对有修改的目录确认实际修改人和用途，将需要保留的工作收成独立提交或私有备份；
   不将数据库、凭据、聊天日志和任务产物上传公开仓库。
3. 对干净目录逐一核对关联 PR、独有提交和未跟踪但被忽略的有价值数据。
4. 对失效登记先查看 `git worktree prune --dry-run`，确认只影响已失效元数据。
5. 在明确清理范围后再移除目录或登记；保留清理记录。不得用批量强删代替核对。

## PR 1 的边界

本 PR 增加只读观测与版本身份，沿用现有端口及数据位置。统一启动、完整隔离、
alpha 同步保护和共用测试入口属于 PR 2；审批与发布流程归 PR 3。
