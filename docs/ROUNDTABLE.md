# 讨论技能

在普通聊天中输入 `/discuss 议题`，例如：

```text
/discuss 咱们的 eval 应该怎样设计？请比较方案并保留待验证事项。
```

主页面不再提供“任务 / 多人讨论”模式切换、角色选择和六阶段流程面板。讨论在后台执行；聊天中只显示简短状态、必要的人工选择及最终结果。最终结果的依据和复核处置默认收起，点击“查看依据与复核记录”可查看。用户点过的价值选择会持久化成聊天确认，刷新或继续普通任务后仍保留。

技能定义位于 [`skills/discuss/SKILL.md`](../skills/discuss/SKILL.md)。宿主在创建任务时冻结技能正文及哈希，默认使用项目注册的参与者，继续复用原来的隔离、阶段屏障和结构化提交；没有新增一套模型调度。API 也支持 `{"skill":"discuss","goal":"议题"}`，两个任务创建端点均支持命令入口。

`/api/health` 的 `project_skills` 包含 `discuss` 时才表示后端支持技能入口。前端遇到旧后端会保留输入并提示重启。普通消息仍走普通任务，不根据关键词自动开启讨论；历史 V1 API 与记录保持兼容。

## 六阶段讨论 V2

讨论技能内部使用 `deliberation_v2`，流程为：独立思考 → 分歧讨论 → 用户选择综合者 → 扇入综合 → 原参与者复核 → 最终收束。旧 API 未提供技能和协议时继续使用 V1，旧记录不改写。

- 独立阶段与每一轮交流、复核都使用冻结输入和提交屏障，后完成者不能看到同批次先完成的输出。
- 分歧讨论默认最多 2 轮，可配置 2～3 轮；决策项、关键假设与建议行动不冲突时直接跳过，存在冲突时只邀请与冲突决策项相关的 claim 作者，无增量则提前结束，不把相互认可当作证据。
- 等待综合者时不调用模型，必须由用户明确选择；其他原参与者检查遗漏与误读，有纠正项时最多修订一次。
- 综合稿必须逐项记录非价值问题的技术处置；价值取舍等待用户选择或明确延后。知识候选由用户主动保存，不自动写入长期记忆。
- V2 真实 CLI 目前要求 macOS `sandbox-exec`：受限进程通过宿主 MCP 桥读取固定材料并提交结果，不能读取项目数据库、其他临时工作区或访问本地应用 HTTP 接口。本地模型代理只放行 Provider 配置中的端口。暂不支持的平台会明确拒绝启动 V2；演示模式可离线运行。
- Codex 放行 macOS 管理偏好初始化需要的只读系统缓存；Claude 只继承连接与模型环境配置，不加载用户 hooks；Antigravity 使用独立 HOME、MCP 和数据目录，通过只读 Keychain 别名继续使用现有登录，不修改全局 MCP 配置。
- 单次三人讨论上限为 13 次阶段调用（最多 2 轮讨论），配置 3 轮时上限为 16。没有分歧且复核无发现时实际为 6 次。失败重试与 fallback 合计每个逻辑回合最多 2 次 attempt。
- 中途新增条件先暂存，在阶段边界由用户决定以完整新议题续议，或明确留待下次；不会只注入某一位的上下文。
- 详情 API 与 `/api/health` 提供协议、规则版本与构建指纹，聊天中不常驻显示这些运行参数。
- 失败提示可展开查看各 Agent、Provider、脱敏错误类别、退出码和已尝试次数。原始 CLI 输出和封存正文不展示。
- 本地开发可用 `./scripts/start.sh --no-install --no-open --reload`，让后端源码变化自动重载；运行中的讨论如被重载中断，会保留提交并等待明确重试。

设计及验收契约见 [V2 SPEC](plans/2026-10-19-roundtable-v2-deliberation-spec.md)。离线 HTTP 验收：

```bash
.venv/bin/python scripts/smoke_deliberation.py
```

下面保留 V1 的实现与历史验收说明，仅适用于 `roundtable_v1`。

## 三阶段目标

| 阶段 | 交付 | 当前状态 |
| --- | --- | --- |
| RT-1 | 每轮共享上下文、插话持久化、旧记录兼容 | [PR #7](https://github.com/Buaixihongshi/AgentClub/pull/7) 已合入 |
| RT-2 | 圆桌创建、预算、A2A、插话与控制、并发及恢复 | [PR #8](https://github.com/Buaixihongshi/AgentClub/pull/8)；通过 HTTP 可完整验收 |
| RT-3 | 模式选择、参与者、运行状态、控制按钮、继续讨论与体验验收 | 本次前端实现，堆叠在 RT-2 之上；待 PR 审查合并 |

设计依据：[三阶段方案 PR #6](https://github.com/Buaixihongshi/AgentClub/pull/6)。三位角色沿用[人格约定](PERSONALITIES.md)，不设固定主持、执行或反对岗位。RT-1 的上下文预算沿用[共享对话说明](SHARED_CONTEXT.md)，本阶段不据未实测的字符数推算模型费用。

## 前端入口与运行状态（RT-3）

任务室顶部提供“任务 / 多人讨论”切换。圆桌模式下展示三位角色的姓名与性格标签（通透机敏、坦率好奇、松弛笃定），默认全选，按勾选顺序参与；界面只呈现性格差别，不出现主持、执行者或反对者等固定岗位。

- 参与者 2～3 人才能开始；少于 2 人时提交按钮禁用并提示原因。默认回合预算按 `2 × 人数 + 1` 显示，最后一格留给总结。
- 提交后模式与参与者在讨论结束前锁定，锁定原因与恢复方式写在面板中。
- 讨论进行中，输入框提交按钮变为“发送插话”；圆桌关闭 `@` 自动补全，点名改由“点名先回应（可选）”下拉传递 `target_agent_id`。每次插话携带稳定 request_id，重试不会重复入队。
- 状态面板完全由 `MissionDetail.discussion` 与 SSE 快照驱动：阶段、`已用 x / 最多 y 个回合（含总结）`、参与者、当前发言、未回应插话、pending_control、总结消息条数、mission 状态与 end_reason。
- “本轮后收尾”由系统按发言顺序选择总结者；“本轮后停止”结束讨论。两者都是回合边界控制，面板明确说明不会立即停止当前 CLI，`pending_control` 期间按钮禁用，避免重复提交。
- 收到 409/422 时保留用户输入并给出可操作的中文提示，同时刷新会话状态；讨论结束后面板显示结束原因和“继续讨论”入口，点击后沿用原参与者并把议题回填到输入框。

前端不显示无法推算的精确费用或完成时间，不伪造流式输出，也不把 pending 请求描述成已经停止。运行状态、控制按钮与终态文案由 `frontend/src/lib/roundtableView.ts` 统一格式化。

## 创建与继续

`POST /api/missions` 和 `POST /api/conversations/{conversation_id}/missions` 使用同一创建契约。前者可传 conversation_id，缺省则原子创建新会话；后者返回原有 conversation detail 结构。普通任务仍默认 `room_mode=task`。

```json
{
  "goal": "Club 下一步该做什么？",
  "room_mode": "roundtable",
  "request_id": "start-001",
  "discussion": {
    "participant_ids": ["chulan", "baobao", "wangye"],
    "max_turns": 7
  }
}
```

参与者有序、不重复，2～3 位且须在配置中。max_turns 缺省为 `2 × 人数 + 1`，最少 `人数 + 1`，最多 12，并受 `YIREN_ROUNDTABLE_MAX_TURNS` 服务端上限约束（默认 12）。它独立于普通任务的 `YIREN_MAX_TURNS`。圆桌不能混用 target_agent_id(s)，task 不能携带 discussion，game 尚未开放；非法配置返回 422。

两个入口都以 SQLite `BEGIN IMMEDIATE` 原子检查归档/活动任务，再创建 mission、首条用户消息与请求记录。同一会话已有 pending/running 任务时，新建返回 409（普通任务也适用）；活动会话归档同样返回 409。request_id 可选，提供时相同范围和内容重试返回已有 mission，不重复启动线程；不同内容复用 ID 返回 409。客户端应始终提供稳定 request_id。已有会话的两个入口共享幂等范围；无 conversation_id 的创建使用独立的新会话范围。

继续讨论通过原 conversation 再创建新 mission。客户端提交参与者与议题；旧任务、旧预算和旧消息不被复活或覆盖。

## 发言、预算与邀请

默认三人 `A → B → C → A → B → C → 总结`，两人 `A → B → A → B → 总结`。自定义更高预算时继续按参与者顺序交流，最后一格预留总结。总结者默认是最近一次成功发言者的下一位；用户收尾可临时指定。没有成功发言时不调用模型总结。

每次领取 turn 即占用一格，包括用户插话回应、A2A callee/resume 和失败回合。一个 turn 内的 Provider fallback 共用这格；每次 attempt 的耗时/用量仍独立记录。正常预算结束进入 closing，不算故障。正常交流必须保留尚未发言者各一格及总结一格。

邀请仍只走 MCP `invoke_agent`，不解析正文中的名字或 @。圆桌调用的双方、活动 turn 和当前 Provider 都从持久化状态校验；stdio 环境变量即使包含更多角色也不能扩展参与者。

- `fire_and_forget` 的目标已有待执行回合时，保存邀请 purpose 到该角色下一次发言，不追加重复回合。结果为 `status=scheduled, skipped=true, queued_position=0, reply_pending=false`。
- 其余邀请在事务中接纳，返回 `status=accepted, queued_position=1, reserved_turns=1|2, reply_pending=false|true`。wait_reply 额外返回 `resume_position=2`，表示紧接 callee 的续答位置；后续嵌套调用或用户控制可改变实际执行顺序。
- `remaining_turns` 表示排除当前已领取回合和总结格后的剩余总格数，包含已经承诺的队列格；不是可自由追加的数量。
- wait_reply 同时预留 callee 和 sender resume；嵌套时保留已有队列、未发言者和总结空间。每个 sender turn 只接纳一条不同的 wait_reply。相同 sender turn/目标/purpose/mode 重复调用返回原接纳结果；等待环、预算不足、非参与者、收尾中等返回 MCP tool error 并记录原因。
- 已接纳不代表已发言：用户插话/收尾/停止或故障可能取消队列，`a2a.cancelled` 写明被取消步骤和目的。普通任务仍使用原来的邀请路径。

圆桌的进程内和 stdio 工具都调用同一个数据库策略，不再分别推算预算。回合开始前刷新共享对话，并加入参与者、阶段、已用/总回合数、邀请目的、合并的用户补充；resume 额外获得对应 callee 的正式回复。

## 插话与控制

插话继续使用 `POST /api/missions/{id}/interruptions`。圆桌要求 request_id，任务模式保持原有参数兼容。

```json
{"content":"只有一周开发时间","target_agent_id":"wangye","request_id":"input-001"}
```

同一回合开始前收到的插话合并到该回合。若有多个点名，最新点名优先；实际回答者在 interrupts 视图中可见。目标尚未开场时前移其开场机会，不覆盖其他人的首次发言。已发言目标只有在预算允许时才另占交流格；无法分别回应的点名记录为 deferred，并送入总结。所有插话正文保留在共享历史中。

`POST /api/missions/{id}/discussion-controls`：

```json
{"action":"finish","request_id":"finish-001"}
```

或 `{"action":"stop","request_id":"stop-001"}`。前端收尾时不指定总结者；API 仍兼容可选的 summary_agent_id，传入时只允许用于 finish，且必须是参与者。

- finish：当前回合结束后取消原交流/A2A 队列，转一个总结回合。
- stop：当前回合结束后结束讨论，不再领取新回合。stop 优先于 finish。
- 响应 202 表示请求已接纳；`pending_control` 持续显示等待当前回合结束，不表示 CLI 已立即退出。
- 开始收尾、已请求收尾/停止或已终止时，新插话返回 409。已收尾时再次请求 finish 返回 409，stop 仍可接纳。
- 相同 request_id 与内容重试先返回既有结果，终态后也可重放；不同内容复用 ID 返回 409。
- 已接纳输入最终标为 handled、deferred 或 cancelled；终态不会留下永久 pending。用户正文中的“停止”不会直接触发宿主控制。

## 状态、事务与恢复

`build_mission_detail` 及 SSE 新增可空 `discussion`：

```json
{
  "participant_ids": ["chulan", "baobao", "wangye"],
  "phase": "exchange",
  "turns_used": 4,
  "turns_limit": 7,
  "current_speaker": "chulan",
  "current_turn_id": "turn_4_example",
  "pending_control": null,
  "end_reason": null,
  "summary_message_ids": [],
  "unanswered_interrupt_ids": []
}
```

phase 为 opening → exchange → closing → ended，提前停止可直接 ended。turns_used 包含当前已领取回合。summary_message_ids 引用正式消息，不另存一份总结正文。unanswered_interrupt_ids 记录未单独按点名回应或被取消的插话；若已在总结处理，可结合 interrupts.handled_in_summary 判断。

状态保存在 append-only `discussion.state` 事件中，以最近一次状态事件重建；`discussion.started`、`phase_changed`、`turn_claimed`、`control_requested`、`control_handled`、`invitation` 和 `ended` 保留可审查的过程记录。队列与 turn lease 的权威来源是 SQLite，不是 runner 内存。

所有控制接纳、turn 领取、终态提交，以及有副作用的 MCP 工具都使用同一写事务串行校验。当前回合可在 stop 请求之后完成；过期 turn 或过期 Provider attempt 的消息、产物和邀请被拒绝并记录 tool.error。MCP update_status 不能自行结束圆桌，结束权属于宿主。

end_reason：`budget_exhausted`、`user_finish`、`user_stop`、`provider_failed`、`summary_failed`、`service_restart`、`runner_failed`；异常为 blocked，正常结束为 completed。极端无成功发言的预算截止另记 `no_successful_turn`。服务启动将遗留 pending/running 圆桌置为 blocked/service_restart，撤销旧 lease，不自动重跑；可在原会话新建讨论。

部署沿用本地单后端进程模型，不应让多个独立应用实例共享数据库并分别执行启动恢复。本阶段不实现立即杀死当前 CLI、分布式 worker 或自动恢复副作用任务。

## 验证

```bash
python -m pytest backend/tests -q
python scripts/smoke_roundtable.py --fake
python scripts/smoke_onboarding.py --fresh --fake
python scripts/smoke_conversation_flow.py --fake
python scripts/smoke_real_cli_mission.py --fake

cd frontend && npm test && npm run build
```

前端用 `roundtableView.test.ts` 覆盖阶段/结束原因/控制/预算文案，`DiscussionSetup`、`DiscussionStatus` 覆盖锁定态与控制按钮，`App.roundtable.test.tsx` 覆盖三人与两人启动、参与者不足、运行中锁定、插话 request_id 与点名、收尾/停止、刷新恢复与 SSE 终态、继续讨论沿用参与者、409 保留输入、Provider 失败结束，以及任务模式兼容。

`test_roundtable.py` 覆盖双方创建入口、事务回滚、并发/幂等、默认/极限预算、fallback、嵌套 wait_reply、开场机会保留、目的去重、插话批量处理、收尾/停止、终态拒绝、重启及迟到 MCP；关键边界同时运行进程内和实际 stdio 子进程用例。

smoke 的 --fake 使用确定性的“观点 → 回应 → HTTP 插话 → 修正 → 总结”脚本，检查实际 prompt 中的区别性事实，并验证最终 HTTP/SSE 视图。默认不调用真实模型。

前端运行状态、控制 pending 文案和终态恢复也在真实浏览器中核对过：圆桌启动后展示阶段、预算、参与者、点名下拉与两个控制按钮；请求收尾后显示“等当前回合结束后开始总结”；收尾完成后面板给出结束原因并可继续讨论。演示 Provider 每轮近乎瞬时完成，因此“插话被接纳并进入某回合”的分支由上面的前端用例覆盖，未在演示模式的实时页面里复现。

`python scripts/smoke_roundtable.py --real --timeout-seconds 300` 是显式 opt-in，使用临时配置将三位角色接到已登录的 Codex CLI，经 stdio MCP 运行。真实发言的人格、质量和实际费用需另行验收；离线测试不代表已经完成这项验收。
