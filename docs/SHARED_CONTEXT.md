# 共享对话与用户插话（RT-1）

本次实现[多人讨论方案 PR #6](https://github.com/Buaixihongshi/AgentClub/pull/6)的第一阶段。已有任务模式、人格和发言顺序继续适用；圆桌模式、统一回合预算、收尾/停止控制和界面入口留给 RT-2 / RT-3。

## 数据与读取约定

- 每个 Agent 回合构建 prompt 前重新读取对话。同一 conversation 的正式发言跨 mission 可见；没有 conversation 的旧任务读取自身消息。
- `domain/history.py` 的只读投影由 prompt、MCP `read_context`、mission HTTP/SSE 和 conversation 历史共用。SQLite 显式读事务保证 mission、消息和事件来自同一快照。
- 时间线以数据库递增事件 ID 排序；消息 ID 负责身份与关联。缺少对应事件的历史 message 行才以 `(created_at, id)` 稳定排序，追加到时间线末尾。不按正文相等去重，也不从角色名称推断回应关系。
- 新插话在一个事务内写入 user Message、message 事件和带 `message_id` 的 interrupt 事件。写入失败整体回滚。路由、待回应/已处理状态仍由 interrupt 事件驱动。
- 旧的 event-only interrupt 投影为 `id=interrupt:<event_id>` 的虚拟用户消息。读取不会改写历史数据库。已处理插话仍保留在历史中。
- HTTP/SSE 的 `messages` 与 `events` 各包含一次该用户消息，使用同一 ID；`events` 不再额外输出一条作为聊天气泡的 raw interrupt。`interrupts` 数组继续提供路由与处理状态。conversation 历史使用相同 ID，现有前端合并逻辑即可去重。
- 普通对话排除接入验证 mission（含旧标题格式）；prompt 和 `read_context` 只包含 user/agent Message 与用户插话。stdout、partial、thought、tool trace 和系统控制文案不进入共享对话。验证任务的 MCP 读取隔离在该任务内。

Agent ↔ 宿主仍只走原有 MCP。没有新增 Agent HTTP 回调，也没有将 stdout 转换为正式消息。

## 上下文裁剪

prompt 的共享历史节选默认最多 **8 条、单条正文 1,200 字符、合计 6,000 字符**（含作者、ID、标题和截断标记）。这里是 Python 字符计数，不是 token 或费用上限，也不是整个 prompt 的长度限制；当前任务议题仍由 runner 单独完整传入。

分配预算时，依次优先最新用户输入、最新插话、明确 `sender_turn_id` 所指回合的消息，然后补充近期发言。展示时恢复事件顺序。这样“继续讨论”这条新输入不会把先前最新插话挤出窗口。优先保留并不代表旧条件永远有效，角色仍需结合后来输入理解变更。

单条超过预算时保留约 2/3 开头、1/3 结尾，中间插入 `…[已截断]…`，作者与消息 ID 保留。不调用额外模型总结历史。中间内容仍可能被裁掉；MCP `read_context` 从同一投影按条数取全文（默认 20 条，传输入口最多 100 条），供角色主动补读。它与 prompt 共享身份、顺序和过滤规则，但不对正文做字符截断。显式 A2A 回应内容仍由原有 runner 路径传入。

## prompt 前后对比

使用 `5449cb3` 的原始 `build_conversation_context` 与当前实现，在临时 SQLite 中写入固定消息后比较；不调用模型。以下仅展示共享历史节选。

短对话场景：用户提问后，张楚岚发言，用户补充离线条件，再轮到下一位。旧 runner 在任务开始时冻结上下文，因此下一位看到的仍是 21 字符：

```text
最近对话：
用户：Club 下一步做什么？
```

当前实现每轮刷新，得到 91 字符：

```text
最近对话：
用户：Club 下一步做什么？ [id=topic]
张楚岚：先让每位伙伴读到最新对话。 [id=first]
用户：补充条件：全程离线 [id=interrupt:4]
```

长对话 fixture 与 `test_bounded_prompt_prioritizes_user_and_reply_then_recent_speech` 相同：用户正文为 `关键条件开头 + 长条件 × 600 + 关键条件结尾`，接一条明确被回应的消息和 12 条约 1,800 字符的普通发言。对比双方均在这些消息写入后构建，单独检查裁剪策略：

| 共享历史节选 | 字符数 | 展示条数 | 用户条件头/尾 | 被回应观点 |
| --- | ---: | ---: | --- | --- |
| 旧实现：最近 8 条，每条 220 字 | 1,829 | 8 | 均丢失 | 丢失 |
| 当前默认预算 | 5,999 | 6 | 均保留 | 保留 |
| 当前紧预算：最多 4 条 / 2,200 字符 | 2,199 | 3 | 均保留 | 保留 |

条数是上限，字符预算不足时可能更少。测试断言真实传入 B/C 的区别性事实、新旧插话跨 mission 可见、API/SSE 身份一致、长消息裁剪优先级及确定性；不以“请参考历史”这样的提示语替代验收。

## 验证入口与后续边界

`backend/tests/test_shared_history.py` 覆盖逐回合共享、旧数据兼容、新插话事务回滚、并发写入时的读快照、事件顺序、验证/诊断过滤、跨任务保留条件和有界裁剪。`test_mcp_server.py` 通过真实 stdio 子进程读取新旧插话，核对工具传输结果。

RT-1 不改变插话的生命周期接纳规则：终态拒绝、请求幂等、会话活动锁、收尾/停止与预算竞争仍属于 RT-2。因此这次完成的是多人讨论的上下文基础，还不是完整圆桌体验。

RT-2 与 RT-3 随后补齐了后端圆桌与前端入口，契约、状态字段和验证入口见 [多人讨论文档](ROUNDTABLE.md)。
