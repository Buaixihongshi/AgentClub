# 共享开发任务

> 本文记录旧版共享任务实验的操作方式。当前用法见[自主工作](AUTONOMOUS_WORK.md)，目标设计见[自适应多 Agent 自主推进 Spec](plans/2026-10-23-adaptive-agent-autonomy-spec.md)。本文的固定审核、手动启用和重启暂停规则只适用于历史协议。

共享开发任务把需求、分工、执行和审核保存到 SQLite；聊天和 mission 只负责沟通与一次执行尝试。关闭页面或结束聊天不会清除任务。

设计参考 [Clowder 的共享工作与持久责任](https://github.com/zts212653/clowder-ai) 和 [其独立审核流程](https://github.com/zts212653/clowder-ai/blob/main/cat-cafe-skills/request-review/SKILL.md)，实现复用 AgentClub 自己的 MissionRunner、Provider Router 和 MCP 宿主身份。

## 页面操作

1. 进入会话，展开「共享开发任务」。
2. 编写开发约定：原始需求、预期结果、开发范围、本次不做、逐行验收项、验证命令。命令为 JSON 参数数组，例如 `[["npm", "test"], ["npm", "run", "build"]]`，在服务所在 Git 工作区执行，不经过 shell。
3. 保存草案，启用具体版本。草案不会自动触发模型或执行命令。
4. 创建共享任务，选择执行人、不同的审核人、覆盖的验收项以及前置任务。每项验收条件都必须至少由一个任务负责。
5. 点击「启动执行与本地提交」。这允许角色执行约定范围内的工作、运行已展示的验证命令并创建本地提交；不包含自动合并或发布。
6. 后台依次执行：领取任务 → 保存进度 → 提交候选 SHA → 角色回合结束 → 宿主验证 → 独立审核 → 完成或返工 → 推进后续依赖任务。聊天会同步展示执行回合。

「暂停后续执行」不强制杀死正在工作的 CLI 或验证命令，当前回合可以保存结果，但不会再启动下一轮。阻塞后可查看执行记录并重试；已暂停时还需点击启动。服务重启后自主推进暂停，未完成的尝试转为阻塞，不自动重放未知的文件副作用。

## 约定、任务与证据

- 开发约定保存原始需求快照、作者、时间、版本、稳定 ID、验收项和内容摘要。修订追加版本，旧任务、证据与审核继续指向旧版本。
- 每张任务保存一个执行人和一个独立审核人、验收项、依赖、进度、阻塞原因、每次执行的 mission 和当前阶段。只有前置任务审核完成后才执行后续任务。
- 提交时必须有干净的 Git 工作区。宿主在角色回合结束后运行约定命令，不占用 MCP 写事务。运行前后检查 HEAD 和工作区；验证产生未提交改动或 HEAD 变化也会判为未通过。
- 验证记录包含约定 ID 与摘要、验收项、完整提交 SHA、执行角色、mission、turn、命令、退出码、输出摘要和时间。完整输出另存 SQLite，任务板显示末尾 24 KB 并提供完整日志入口。
- 审核必须来自独立审核人，覆盖任务的全部验收项，引用当前候选 SHA 和验证记录，并保存结论与说明。代码已变化或工作区不干净时不能批准。
- 已完成任务的审核只适用于记录中的约定和 SHA。后续提交不会改写历史结论；页面会明确提示「历史版本的审核」。它不代表后续整个代码库都已通过审核。

## MCP

新增 `shared_work` 工具。mission、角色、turn 和 Provider 由宿主注入，模型参数不能切换身份或工作范围。

- `read`：读取当前任务板及 `revision`。
- `propose_contract`：保存约定草案。字段见 `backend/app/work/models.py`。
- `create_task`：保存分工，关联约定、验收项、执行人与审核人。
- `progress`：`data={task_id,text}`，保存检查点。
- `submit`：`data={task_id}`，提交干净的候选版本；此时仅进入等待宿主验证，不表示测试通过。
- `review`：`data={task_id,commit_sha,criterion_ids,decision,note}`；`decision` 为 `approved` 或 `changes_requested`。
- `block`：`data={task_id,reason}`，保存阻塞原因。

每次写入都带 `expected_revision`；冲突后重新读取，禁止覆盖新状态。稳定会话 MCP 还要求写操作携带 `operation_id`，同一操作重试复用编号。用户独占启用约定、启动、暂停和重试权限。普通 Agent 聊天、stdout、任务 mission 的 completed 状态均不能代替结构化提交和审核。

## REST 与运行边界

- `GET /api/conversations/{conversation_id}/work`：任务板与当前 Git HEAD。
- `POST /api/conversations/{conversation_id}/work`：`{action,expected_revision,data}`，包括用户的 `activate_contract/start/pause/retry` 操作。并发版本冲突返回 409。
- `GET /api/conversations/{conversation_id}/work/logs/{log_id}`：该会话的完整验证日志。

首版面向本地单用户、当前服务 Git 工作区。所有任务板共享工作区级串行领取锁，避免多角色并行改写同一工作树；没有接入独立工作树、分支合并或发布。每轮调度最多运行 12 个阶段，每张任务最多保留 12 次自动尝试后阻塞，防止无界返工。演示 Provider 保持演示行为，不伪造测试或审核，缺少结构化结果会明确阻塞。

验证入口：`backend/tests/test_shared_work.py`、`frontend/src/tests/SharedWorkBoard.test.tsx`。开发验收约定见 [方案与验收项](plans/2026-10-23-shared-work-contract.md)。
