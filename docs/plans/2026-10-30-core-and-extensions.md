# 回到初心：核心与可选扩展

## 问题与目标

Unity 的初心是让几个 CLI 互通、拥有共同上下文。后来加入的多人讨论（圆桌 RT、`/discuss`
V2）和任务系统（共享工作、自适应调度、工作区/预览/发布）成了默认路径：工具列表里常驻
16 个 action 的 `shared_work`，prompt 里常驻提议任务的指引，界面里常驻「任务」页和讨论入口。
所有者实际使用时，这些功能约束了普通对话，体验变差。另外，`/discuss` 的效果也没有经过真实评测证明。

目标是让默认的 Unity 只剩初心：

- **核心**（始终开启）：共享对话历史与 `read_context`、`publish_message`、`invoke_agent`
  接力、长期记忆/检查点/模型交接、产物读写、观测页与私有执行记录、A/B/C/D 评测。
- **扩展**（默认关闭，可在设置里开启）：讨论（圆桌 + `/discuss` V2）、任务（共享工作及其
  工作区、预览、发布）、维护（agent 自维护仓库）。开启后行为与现在一致。

## 发言协议：本次不改

`publish_message`、铁律 L1 和 MCP 路径的发言指引保持原样，作为核心的稳定边界。依据是日常库
（`8ef68fe`）的只读统计：491 次回合中 63 次 blocked，最大的来源是约 27 次超时；"未调用
`publish_message`"共 12 次，其中 8 次来自已停用的 gemini，claude/codex/antigravity 合计约 1%。
超时类事故里 CLI 本来就没有产出最终回复，"最终输出即发言"也救不回来；而现行协议换来的是
可审计性和过程叙述不混进聊天。

重新评估的条件：等 #68 的每轮执行记录积累一段时间后，统计被阻塞回合中已产出像样最终回复的
比例（可挽救率）。比例高，再考虑"publish 为主路径，正常退出但未发言时由宿主采集并标注来源"；
比例低就维持现状。更值得单独做的是超时体验（阶段性发言，或超时后由宿主在聊天里留下进度与产物），
不并入本次重构。

## 分两步

### 第一步：扩展开关，默认关闭（本 PR）

- `Settings` 新增 `discussion_enabled`、`tasks_enabled`，与已有的 `maintenance_enabled`
  一起作为运行时字段，从 `hub_settings.json` 的 `runtime` 读取；环境变量
  `YIREN_DISCUSSION_ENABLED` / `YIREN_TASKS_ENABLED` / `YIREN_MAINTENANCE_ENABLED`。
  三者默认都为 false（维护原默认 true）。
- 设置接口 `GET/PATCH /api/hub/settings` 增加 `extensions` 段；设置页新增「扩展」页签。
  切换即时生效，不需要重启：新请求、新回合和调度循环都会重新读取设置。
- 关闭时：
  - 讨论：拒绝创建讨论 mission（`/discuss`、圆桌）；输入框不提供 `/discuss` 与讨论选择。
  - 任务：普通对话的 MCP 工具列表不含 `shared_work`，prompt 不含提议任务的指引；拒绝创建
    任务、提议和执行；调度循环不领取新动作；界面隐藏「任务」页和对话里的任务卡。
  - 维护：沿用已有 `maintenance_enabled` 门禁，界面隐藏维护入口。
- 已有数据不删除、不迁移。关闭只停止新的创建和调度，已开始的讨论/任务回合按原逻辑结束；
  重新开启后，暂停的任务从持久状态继续。历史会话里已有的讨论消息仍作为普通消息显示。

### 第二步：代码迁入扩展，核心不再 import

代码迁入 `backend/app/extensions/{discussion,tasks,maintenance}/`，行为、API 与数据不变：

| 原位置 | 新位置 |
| --- | --- |
| `orchestration/{roundtable,deliberation,deliberation_runner}.py` | `extensions/discussion/` 同名模块 |
| `adapters/deliberation_cli.py` | `extensions/discussion/deliberation_cli.py` |
| `domain/deliberation.py` | `extensions/discussion/models.py` |
| `api/discussion_projection.py` | `extensions/discussion/projection.py` |
| `api/mission_requests.py` 的讨论配置与 `DiscussionControlRequest` | `extensions/discussion/requests.py` |
| `api/missions.py` 的 `discussion-controls` 路由 | `extensions/discussion/api.py` |
| runner 的圆桌回合、MCP 圆桌守卫与 V2 读写/结果工具 | `extensions/discussion/{roundtable_runner,tools,hooks}.py` |
| `work/*` | `extensions/tasks/` 同名模块 |
| `api/{shared_work,tasks}.py` | `extensions/tasks/api/` |
| runner 的自适应回合、`shared_work` 工具与工作限制 | `extensions/tasks/{runner,tools,hooks}.py` |
| `maintenance/*`、`api/maintenance.py` | `extensions/maintenance/`（`api.py`） |

核心保留的挂接点集中在 `backend/app/extension_points.py`：接管 mission（含创建、失败、
收尾、打断、占用会话）、回合前后（尝试检查、取消、演示/API/路由回合）、工具守卫与额外工具
（含 MCP 列表/拒绝/处理器）、prompt 片段、讨论与工作在核心视图里的展示。路由、恢复与后台循环
由组合根 `main.py` 通过 `backend.app.extensions` 接入；MCP 子进程由父进程经
`YIREN_MCP_EXTENSIONS` 传入同一组扩展。`backend/tests/test_extension_boundary.py`
扫描 `extensions/` 与 `main.py` 以外的核心模块，确认不 import `backend.app.extensions.*`。

仍留在核心、不经 import 的数据层知识：`runtime_activity.py`、`storage/execution.py`、
`settings.py` 的扩展开关，以及会话详情/删除与普通 mission 创建中对 `work_boards`
表的只读 SQL。它们不改数据，后续可按需再收拢。

## 风险与恢复

- 默认关闭会让升级后的日常实例看不到任务页。如果仍有进行中的任务，所有者需在设置 →
  扩展里开启任务。数据没有任何改动，开启即恢复。
- 已有检查和测试中依赖讨论/任务默认可用的部分，会在测试配置里显式开启扩展，不降低覆盖。
- 回滚：回退本 PR，或在设置里开启全部扩展，即可回到原行为。
