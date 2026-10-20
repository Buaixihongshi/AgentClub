# 观测升级复盘（STAR）

> 日期：2026-10-20
> 范围：把「右侧栏单会话追踪」升级为「设置页全局观测」，并补齐三个 CLI 与 MCP 之间缺失的观测字段。
> 相关文件：`backend/app/observability/`、`backend/app/api/observability.py`、`frontend/src/components/ObservabilityOverview.tsx`、`docs/OBSERVABILITY.md`

## Situation（背景）

右侧栏的「追踪」只能看**当前会话**，而且信息是 UI 临时拼的：回合、工具、A2A 混在一张 swimlane 里，没有全局视角，也无法回答“这套系统最近跑得怎么样”。

更严重的是数据层：三个 CLI 的原生工具调用大部分没有落进事件表。

- codex / claude 的 `TOOL_ATTEMPT` 不带 `activity_id`，被 `if ev.activity_id` 直接丢掉（实测 codex 125 回合、claude 105 回合，`provider.activity` 为 0）；
- 工具结果只写一句摘要（`tool.result` 中位 54 字符），没有 output；
- 没有 `args`、`ok`、`duration_ms`，MCP 的 `tool.call` / `tool.result` 也配不上对；
- 没有模型名，只有 provider；
- 用户实际看到的面板里，每一步都是“只有调用记录，没有内容”。

## Task（任务）

1. 让观测成为**全局**能力：跨全部会话统计，不再绑定某个 mission；
2. 把它放进**设置页的独立页签**，画幅接近全屏；
3. 补齐 CLI ↔ MCP 的观测字段并落库；
4. 总览要能下钻：点最近失败 / 最近回合 / 表格行，返回该回合的步骤细节；
5. 删掉右侧栏入口，避免两个入口并存。

## Action（过程）

### 1. 字段审计与补齐

先列“三个 CLI 各自能报什么”，再对照 `events` 表实际存了什么，发现 6 类缺口：

| 字段 | claude | codex | antigravity | 处理 |
|---|---|---|---|---|
| `model` | `modelUsage` | 不提供 | 不提供 | 新增 `provider.session` 事件 |
| `args` | `tool_use.input` | `arguments` / `command` | `tool_info.parameters` | 落进 `provider.activity.args` |
| `output` | `tool_result` 块 | `aggregated_output` / `result` | `tool_info.output` | 解析并落库，脱敏截断 |
| `ok` / `error` | `is_error` | `status` / `exit_code` | step state | 落成布尔 + 原文 |
| `duration_ms` | started→completed | 同左 | 同左 | 按 `activity_id` 配对计算 |
| `call_id`（MCP） | — | — | — | `tool.call` 生成，`result/error` 自动配对 |

实现要点：

- `StreamEvent` / `UsageRecord` 扩展字段（`tool_args` / `tool_output` / `tool_ok` / `model` 等）；
- claude 新增解析 `type:"user"` 的 `tool_result` 块（此前完全忽略）；
- codex 从 `arguments` / `command` / `aggregated_output` / `exit_code` 取值；
- antigravity 不再主动丢弃 `tool_info.output`；
- 落库前统一 `sanitize_tool_args` / `sanitize_tool_output`：密钥形状打码、args 800 字符、output 2000 字符；
- 同一 `activity_id` 的 started / completed / result 在读取侧合并成一步。

### 2. 全局聚合与接口

新增：

- `GET /api/observability/overview?window=all|today|7d|30d&limit=12`：跨全部会话统计总量、检查项、覆盖率、花费、Agent / Provider、14 天趋势、最近失败与最近回合；
- `GET /api/observability/turns/{turn_id}`：按 `events.payload.turn_id` 反查 mission，复用 `build_turns` + `run_checks`，返回单回合的步骤 / 检查 / 回复；
- `Repository.mission_id_for_turn()` 支撑反查；
- 窗口按**本机时区**切天（应用跑在用户电脑上），不是 UTC。

### 3. UI：先 demo，再实现

先出三版静态 demo（总览仪表盘 / 工作台 / 明细审计），用真实数据截图评审。选定 **v2 工作台为默认页 + v3 明细表做下钻**，随后落地：

- 设置弹窗放大到 80vw × 80vh（1440×960 下 1152×768）；
- 默认页：左栏总量 / 覆盖率 / 14 天趋势，右侧检查项、Agent / Provider、最近失败、最近回合；
- 下钻：全部回合表 → 点行进入单回合细节（步骤、参数、输出、错误、检查项、回复），可逐级返回；
- 删除右侧栏入口、`ObservabilityDialog`、`useObservability` 与对应测试/样式，会话区改为单列。

### 4. 踩过的坑

- **实时进程没重载**：8000 上的 uvicorn 没有 `--reload`，补丁写完但页面一直显示旧数据。之后统一用 `--reload` 验收。
- **codex 的 `duration_ms` 常常是 1ms**：started/completed 两帧几乎同时到达，耗时不代表真实执行时间，只能在有 CLI 自报时可信。
- **v2 讨论走隔离进程**，原生工具完全不进 `provider.activity`，所以 v2 回合“原生工具可见”为 0%，是架构边界。
- **旧数据不回溯**：字段只对新回合生效。
- **工作区里有大量未提交的 v2 讨论代码**，观测改动依赖它们，PR 无法单独构建。

## Result（结果）

- 全局观测：61 个会话 / 147 个任务 / 342 个回合 / 919 次工具调用 / $74.31，一屏可读；
- 单回合可下钻到步骤级，原生调用从 0 可见变成能读出命令、输出、退出码（codex `ls` 示例：`→ exit 0 · 68 chars`，claude `Bash · find …` 带 494ms）；
- 侧栏入口删除，会话主区改为单列，构建产物 394KB → 385KB；
- 测试：后端 **802 passed, 1 skipped**；前端 **231 passed**；`npm run build` 通过；
- 文档：[docs/OBSERVABILITY.md](../OBSERVABILITY.md) 记录了字段约定、设置页签、验收标准与已知边界。

## 后续

- codex 的耗时以 CLI 自报为准，否则标注“估算”；
- v2 隔离进程的原生工具需要桥接层上报，当前不覆盖；
- 观测页的搜索、导出、列排序尚未实现；
- 若日志继续增长，`overview` 需要加缓存（当前全量扫描约 380ms）。
