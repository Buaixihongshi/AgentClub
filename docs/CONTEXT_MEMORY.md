# 上下文记忆

实现[上下文记忆计划](plans/2026-10-25-context-memory.md)：角色拥有跨会话的长期记忆、会话内短期检查点，并在模型切换时获得显式交接；用户有统一的管理入口。本文描述实际行为与边界。

## 数据与权威性

- **长期记忆**按角色存于 `<环境状态>/data/memory/agents/<agent_id>/<entry_id>.md`（设置项 `memory_root`，env `YIREN_MEMORY_ROOT`；受管环境随 `.yiren/<profile>` 隔离，不进 Git）。文件是权威：frontmatter（id、title、tags、status、revision、时间戳、source_refs）加正文。
- **SQLite 只存派生与执行状态**：`memory_entries` 为可重建索引；`context_checkpoints` 为每 (conversation, agent) 一行滚动短期检查点；`model_state`/`model_handovers` 记录角色当前有效模型指纹与交接。
- 写入次序为文件先行（临时文件 + 原子替换）、索引后行；装配与 MCP 读取直接读文件，不依赖索引新鲜度。`sync`/`rebuild`（API `POST /api/memory/sync|rebuild`）以文件为准修正索引，手工漂移与文件缺失都会报告。崩溃残留最多影响索引，不影响装配。
- 检查点与交接为单事务 SQLite 写；交接的确认（acknowledged）在回合实际执行后写入，崩溃只会重发不会丢失。

## 统一上下文装配

`backend/app/memory/assembly.py` 是唯一装配点，同时供 runner prompt（普通任务、圆桌、自主工作三条路径）与 MCP `read_context` 使用，按固定顺序渲染：

1. 【模型交接】未确认的切换记录（前 → 后、原因、时间），提示按记忆与检查点接续，不因换模型重置已确认事实；
2. 【长期记忆】有效条目（默认 6 条、2,400 字符预算，含 revision 与截断标记）；
3. 【短期检查点】本会话该角色的滚动检查点（默认 1,600 字符）。

对话节选与产物清单维持原有预算和优先级不变。Provider 验证任务不注入任何记忆节，其 `read_context` 保持任务内隔离。

## MCP 工具

- `memory(action, data)`：仅操作调用角色自己的记忆。`read` 列表或按 `entry_id` 读全文；`write(title, body, tags?, source_refs?)` 新增；`revise(entry_id, title?, body?, tags?)` 修订（revision 递增）；`forget(entry_id)` 标记忘记并移出一切后续上下文。写入/修订/忘记分别记录 `memory.written/revised/forgotten` 事件。
- `save_checkpoint(text, source_message_ids?)`：保存本会话滚动检查点（≤4,000 字符），记录 `checkpoint.saved` 事件；需要普通会话上下文。
- `read_context` 新增 `message_ids`（原文补读，≤50 条）：按消息 ID 精确取未截断全文，结果带 `memory`（条目摘要 + 待交接）、`checkpoint` 与 `missing_message_ids`。V2 圆桌讨论维持只读白名单，新工具在 V2 中不可用。

## 模型切换交接

- 任务启动时宿主比较每个角色的有效 (Provider, 模型) 指纹与 `model_state`；变化即写入 `settings` 原因的交接记录。首次记录只建立基线，不产生交接。
- 轮内 Provider 回退时写入 `fallback` 原因的交接，并在该次尝试的 prompt 末尾追加回退说明；回退不改动 `model_state`（配置指纹未变）。
- 交接在成功送达 prompt 的回合结束后标记确认；下次同设置不再重复。历史可在 `/api/memory/handovers` 与记忆面板查看。

## 用户管理入口

- REST：`/api/memory/overview`（角色计数与一致性状态）、`/api/memory/agents/{id}/entries`、条目 `PUT`/`forget`/`restore`/`DELETE`、`/api/memory/rebuild|sync`、`/api/memory/conversations/{id}/checkpoints`（GET/DELETE）、`/api/memory/handovers`。
- 前端：设置 → 记忆 页签——按角色查看/修订条目、忘记（仅移出上下文，可恢复）与清除（删除文件）、显示已忘记条目、重建索引、本会话检查点清除、最近模型交接与一致性状态。索引漂移会明确提示；该页签与观测页签一样不显示保存栏。

## 验证与边界

- `backend/tests/test_memory_store.py`（权威/索引一致、重建、修订/忘记/清除、长度校验）、`test_context_memory_assembly.py`（三路径装配、验证任务隔离、原文补读、预算）、`test_memory_mcp.py`（工具权限与行为）、`test_model_handover.py`（切换检测、一次注入、回退交接）、`test_memory_api.py`（管理接口）；前端 `MemoryModal.test.tsx`。
- 不做向量检索；不自动调用模型总结历史（检查点由角色结构化保存）；记忆按环境隔离，不跨用户。
