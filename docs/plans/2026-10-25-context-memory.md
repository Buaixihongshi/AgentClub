# 上下文记忆（context-memory）实施计划

日期：2026-10-25。状态：开发中；基线为 main `631b206`（自主任务 PR #26 已合入，直接以 main 为基线，无连续 PR 依赖）。

## 问题

角色每轮只看到有界的共享对话节选和产物清单；跨会话的关键事实、用户偏好、先前结论随窗口滑出而丢失。
换模型/换 Provider 后新实例没有任何交接；会话中断后只能靠重新翻历史。用户无法查看或纠正系统记住了什么。

## 目标（对应用户方案）

1. **短期检查点**：会话内滚动检查点（SQLite），角色经 MCP 保存，装配进后续 prompt，支持中断后接续。
2. **统一上下文装配**：一个装配模块同时产出 runner prompt 各节（长期记忆/检查点/模型交接/对话节选/产物清单）与 MCP `read_context` 的结构化结果，验证任务隔离规则不变。
3. **长期记忆**：按角色的跨会话 Markdown 记忆；角色经 MCP 写入、修订、忘记。
4. **原文补读**：`read_context` 支持按消息 ID 取全文（节选截断后可回读），记忆条目可读全文。
5. **修订与忘记**：记忆条目 revise（revision 递增）/ forget（标记移出装配）；用户可恢复或清除。
6. **模型切换交接**：检测角色有效 Provider/模型变化（设置变更或轮内 fallback），记录交接并注入下一次 prompt 一次。
7. **用户管理入口**：REST API + 前端记忆面板（查看/编辑/忘记/恢复/清除、检查点、一致性状态与重建）。

## 非目标

- 不做向量检索/embedding；索引是可重建的结构化清单。
- 不自动调用模型总结历史（沿用 RT-1 约束）；检查点由角色主动结构化保存。
- 不改变现有对话节选预算、A2A、调度和自主工作协议。
- 不做跨用户/多租户记忆；记忆按环境状态目录隔离。

## 架构与数据

- **权威存储**：`<state>/data/memory/agents/<agent_id>/<entry_id>.md`（frontmatter：id/agent_id/title/tags/status/revision/created_at/updated_at/source_refs + 正文）。设置 `memory_root`（env `YIREN_MEMORY_ROOT`，默认 `data/memory`），随环境隔离。
- **SQLite（派生/执行状态，可重建）**：
  - `memory_entries`：记忆索引（路径/标题/标签/状态/revision/摘要/更新时间），启动与按需 `sync`/`rebuild`。
  - `context_checkpoints`：每 (conversation, agent) 一行滚动检查点（text、source_message_ids、cursor_event_id）。
  - `model_state` / `model_handovers`：角色当前有效模型指纹与交接记录（含 acknowledged 执行状态）。
- **一致性**：写文件先行（临时文件 + 原子替换），后更新索引；索引漂移以文件为准修正。装配读取直接走文件（Markdown 权威），索引服务于管理入口与状态报告。检查点/交接为单事务 SQLite 写，崩溃后无半状态。
- **装配**（`backend/app/memory/assembly.py`）：按预算渲染【长期记忆】（默认 6 条/2400 字符）、【短期检查点】、【模型交接】节；接入 `_run_workflow`、`_run_roundtable`、`_run_adaptive` 三条执行路径与 `read_context`；Provider 验证任务不注入任何记忆节。
- **MCP 新工具**：`memory(action: read/write/revise/forget)`（仅本角色）、`save_checkpoint(text, source_message_ids)`；`read_context` 增加 `message_ids` 原文补读并返回记忆/检查点节。V2 圆桌讨论维持只读白名单，新工具自动不可用。
- **API**：`/api/memory/*`（agents/entries CRUD/forget/restore/purge/rebuild/status、conversations/{id}/checkpoints、handovers）。
- **前端**：记忆面板并入设置弹窗"记忆"页签（沿用观测页签先例，不再有独立头部按钮）：长期记忆列表+编辑、检查点、交接记录、一致性状态。

## 恢复边界与检查

- 中断恢复：装配为纯读；检查点写入单事务；文件写入原子；崩溃残留只影响索引，由 sync 修正。
- 回归：`test_memory_store.py`（权威/索引一致、rebuild、修订/忘记）、`test_context_memory_assembly.py`（三路径装配、验证任务隔离、原文补读）、`test_memory_mcp.py`（工具权限与行为）、`test_model_handover.py`（切换检测与一次注入、fallback 交接）、`test_memory_api.py`（管理入口）；前端 `MemoryModal.test.tsx`。
- 按影响运行 `./scripts/check.sh backend frontend`（含 smoke 视改动而定）。
- 风险分组：MCP 工具面与持久化数据变更 → 合并前需非作者审阅（共享协议与数据完整性）。

## 验收口径

- 演示环境下：角色写入的长期记忆在后续新会话 prompt 中出现；忘记后不再出现；检查点注入同会话后续轮次；`read_context(message_ids=[...])` 返回未截断原文；换模型后首轮 prompt 含交接节且仅一次；记忆面板可完成查看/编辑/忘记/恢复/清除与索引重建。
