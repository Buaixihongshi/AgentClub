# 2026-10-28 A2A 对抗性审查与加固

日期：2026-10-28（Asia/Shanghai，UTC+8）。
审查对象：日常 runtime 当时运行的 main `1404839`（PR #35）。修复分支：`codex/task-a2a-hardening`，基线同为 `1404839`。
审查者：Claude Code（Agent），由用户发起。本文是 Agent 审查与作者自验记录，**不是**仓库要求的非作者审阅，也不是真实模型验收。

---

## 触发

用户要求用对抗性方法评估 A2A 机制。审查只读源码，用离线 fixture 构造攻击场景：真实 CLI 的 MCP 子进程路径会自建 `MCPTools(a2a_enqueue=None)`，写入 `a2a.request`，runner 在回合结束后取件。没有调用真实模型，没有读取或改动日常数据。

## 影响

日常 A2A 走的是子进程路径。仓库原有的 A2A 测试主要覆盖进程内（fake）路径，两条路径对同一契约有两份实现，预算、环检测和拒绝方式不同。原有 125 项相关测试全部通过，没能发现下列问题。

| 编号 | 严重度 | 现象（修复前，离线复现） |
| --- | --- | --- |
| A3 | 高 | 预算 2（D 策略即为 2）时一回合发两次 `wait_reply`：工具两次都返回接纳，取件时把续答回合也计入预算，判定超额，**整个 mission BLOCKED**，没有受邀者执行。 |
| A4 | 高 | 楚岚等宝宝、宝宝再等王也：实际顺序为 楚岚→宝宝→**楚岚**→王也→宝宝。楚岚续答时只拿到宝宝的“稍后再答”，永远拿不到最终答案。 |
| A1 | 中 | 子进程路径同时统计 `a2a.request` 及其 `a2a.invoke`，一次邀请扣两次；预算 2 时第二次邀请即报“深度耗尽 (2/2)”。 |
| A2 | 中 | 目标已在后续流程中而被吸收（`a2a.skipped`）的请求仍消耗预算。 |
| A6 | 中 | `fire_and_forget` 受邀者失败立即终止 mission，后续流程全部丢弃；`wait_reply` 失败反而用兜底文本继续。 |
| A7 | 中 | `purpose` 无长度上限（40 万字符原样进入 prompt）；purpose 还原样出现在 `Turn purpose` 行；回复只有首行加 `>`，后续行可伪装成 `【接续上一轮】` 等宿主段落。 |
| A8 | 中 | 取件信任事件里的 `sender_agent_id`：伪造“王也”的 `wait_reply` 请求被执行，并为王也排了续答回合，且没有对应 `invoke_agent` 工具调用。claude 的 `--mcp-config` 文件（路径在 argv）含 DB 路径与 agent id，环境变量剥离不能阻止有 shell 的 CLI 直接写库。 |
| A5 | 低 | `wait_reply` 环检测只在进程内路径存在。 |
| A9 | 低 | 一回合两次邀请时，第一位受邀者的 `a2a.reply` 关联到第二条 invoke。 |
| A10 | 低 | 闲聊（预算 0）时工具返回 `reply_pending: true`，宿主随后静默丢弃。 |

## 证据

- 攻击用例以修复后的期望行为写成 `backend/tests/test_a2a_adversarial.py`（9 项）。同一文件在 `1404839` 源码上运行：**9 项全部失败**；在修复分支上：**9 项全部通过**。
- 原有 A2A 相关测试组（runner、iron laws、probe、MCP tools/server、圆桌、事件、评测适配、编排、插话，共 191 项）在修复分支上全部通过。
- 完整 `scripts/checks.py backend` 结果见对应 PR 描述。
- 真实 CLI（2026-10-28，修复分支 `b21504d` 的 development 环境，继承日常有效路由：楚岚走 codex，宝宝和王也走 antigravity）：嵌套 `wait_reply` 协议任务按 楚岚 → 宝宝 → 王也 → 宝宝续答 → 楚岚续答 完成。事件链依次为 `a2a.invoke`（楚岚→宝宝）、`a2a.invoke`（宝宝→王也）、`a2a.reply.deferred`（宝宝的外层答复转给其续答）、`a2a.reply`（王也→宝宝：42）、`a2a.reply`（宝宝→楚岚：42 加 1 得 43）。楚岚续答引用 N=42、M=43，mission completed；5 个回合共约 108 秒。本次未经过 claude CLI。

## 原因

1. **同一契约两份实现。** 进程内路径用内存队列计预算（`a2a_used + len(queue)`，把续答也算进去），子进程路径按数据库事件计数（`request` 与 `invoke` 都算）；取件时超额直接把 mission 判为 BLOCKED，工具当时却已回复“接纳”。
2. **队列只有追加语义。** 新请求排到队尾，外层续答排在内层受邀者之前；受邀者回合一结束就把它的发言当作答复，不管它自己是否还在等别人。
3. **请求内容被当作宿主可信数据。** 取件不核对发送者；purpose 与回复以宿主格式拼进 prompt，没有长度和引用边界。
4. **失败策略不对称。** 可选接力失败被当作整个任务失败。

## 修复

| 编号 | 改动 |
| --- | --- |
| A3 | 统一预算单位：一次被接纳的邀请扣 1，续答不计；runner 从 mission 已有 `a2a.invoke` 起算，工具与取件用同一口径。取件超额只记 `a2a.skipped(reason=a2a budget exhausted)`，不再阻断 mission。 |
| A4 | 本回合产生的新步骤先进 `turn_new`，回合结束后插到队首。受邀者自己发起 `wait_reply` 时，外层答复改由它的续答回合给出，并记录 `a2a.reply.deferred`。 |
| A1/A2 | 子进程预算 = 已接纳的 `a2a.invoke` + 本回合尚未取件的 `a2a.request`。宿主把仍有安排的角色通过 `YIREN_MCP_SCHEDULED_AGENTS`（Antigravity 走授权元数据）传给 MCP 服务器，工具当场吸收重复的 `fire_and_forget` 并返回 `skipped`，不写请求、不扣预算。 |
| A6 | `fire_and_forget` 受邀者失败后继续执行剩余队列与流程；结束时 mission 仍标为 BLOCKED，摘要为“接力未完成：…”，失败不会被当作完成。 |
| A7 | `purpose` 上限 4000 字符（schema `maxLength` + 工具校验 + 取件校验）。接力请求与回复逐行加 `>` 引用，并注明“不是宿主指令”；受邀者的 `Turn purpose` 改为宿主固定文案；续答步骤名把 purpose 压成单行并截到 80 字。 |
| A8 | 取件时要求请求发送者等于该回合实际发言的 agent，不符则记 `a2a.skipped` 不执行；超长 purpose 同样拒绝。 |

## 未修复与剩余风险

- **A8 的根本边界未消除。** CLI 以跳过权限模式运行并拥有 shell，与宿主同一系统用户；MCP 配置与数据库对它可读写。本次只让宿主不再信任请求里的发送者字段，具备 shell 的 Agent 仍可直接写库伪造其他事件。根治需要把 MCP 写入改为经宿主签发、按回合校验的通道（类似 `session_server` 的 token），并让 CLI 运行在拿不到数据库的沙箱里。铁律 L3 的“可审计”前提是 Agent 只通过 MCP 写入。
- A5、A9、A10 为低危，本次未改。
- 圆桌路径（`roundtable.py`）有独立的预算与环检测，本次未改；其 `Turn purpose` 仍原样包含邀请 purpose，但 purpose 长度上限已覆盖圆桌入口。
- 真实 CLI 只验证了嵌套 `wait_reply` 这一条关键路径（codex 与 antigravity），预算超额、接力失败和 claude CLI 路径仍只有离线证据。按 SOP，调度/协议变更合并前仍需非作者审阅。

## 防复发

- `test_a2a_adversarial.py` 固定走子进程路径，覆盖预算口径、吸收、超额、嵌套续答、失败继续、长度与引用、发送者绑定和环境变量传递。
- 新增或调整 A2A 规则时，应同时覆盖进程内与子进程两条路径；只在 fake 路径通过不能证明真实 CLI 行为。
