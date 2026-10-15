# 多人讨论后端（RT-2）

## 三阶段目标

| 阶段 | 交付 | 当前状态 |
| --- | --- | --- |
| RT-1 | 每轮共享上下文、插话持久化、旧记录兼容 | [PR #7](https://github.com/Buaixihongshi/AgentClub/pull/7) 已合入 |
| RT-2 | 圆桌创建、预算、A2A、插话与控制、并发及恢复 | 本次后端实现；通过 HTTP 可完整验收，待 PR 审查合并 |
| RT-3 | 模式选择、参与者、运行状态、控制按钮、继续讨论与体验验收 | 待 RT-2 合入后实施 |

设计依据：[三阶段方案 PR #6](https://github.com/Buaixihongshi/AgentClub/pull/6)。三位角色沿用[人格约定](PERSONALITIES.md)，不设固定主持、执行或反对岗位。RT-1 的上下文预算沿用[共享对话说明](SHARED_CONTEXT.md)，本阶段不据未实测的字符数推算模型费用。

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
{"action":"finish","summary_agent_id":"baobao","request_id":"finish-001"}
```

或 `{"action":"stop","request_id":"stop-001"}`。summary_agent_id 只允许用于 finish，且必须是参与者。

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
```

`test_roundtable.py` 覆盖双方创建入口、事务回滚、并发/幂等、默认/极限预算、fallback、嵌套 wait_reply、开场机会保留、目的去重、插话批量处理、收尾/停止、终态拒绝、重启及迟到 MCP；关键边界同时运行进程内和实际 stdio 子进程用例。

smoke 的 --fake 使用确定性的“观点 → 回应 → HTTP 插话 → 修正 → 总结”脚本，检查实际 prompt 中的区别性事实，并验证最终 HTTP/SSE 视图。默认不调用真实模型。

`python scripts/smoke_roundtable.py --real --timeout-seconds 300` 是显式 opt-in，使用临时配置将三位角色接到已登录的 Codex CLI，经 stdio MCP 运行。真实发言的人格、质量和实际费用需另行验收；离线测试不代表已经完成这项验收。
