# 多人讨论第一版：产品与实施方案

状态：提案，尚未实现。基线：`main` 的 `5449cb3`（人格调整 PR #5 已合并）。

## 1. 目标与体验

用户提出一个议题，选择两到三位性格不同的伙伴。伙伴们读取同一份最新对话，自动交流一小段；用户可以插话、点名追问、要求收尾或停止。结束时保留共识、分歧与待验证问题，并可在同一会话继续。

沿用 [人格约定](../PERSONALITIES.md)：三人都有完整能力，不分配永久主持、执行者或反对者。系统管理顺序和预算；总结是临时发言目的，任意参与者可承担。不引入第四位主持 Agent 或额外模型裁判。

**默认方案（待产品评审）**：自动交流，可插话和收尾。一次三人讨论默认最多 7 个 Agent 回合，包含总结；两人默认 5 个。一个回合是一位 Agent 的一次运行，可能包含多条消息；不能把消息数当成回合数。用户消息不消耗 Agent 回合，回应用户的运行会消耗。

典型体验：选择“多人讨论” → 勾选参与者 → 输入议题 → 点击“开始讨论” → 看到正在发言者、当前阶段和已用回合 → 途中补充条件 → 后续发言回应新条件 → 总结后等待用户。

这次只交付规划。第一版完成需包括下述三个实现 PR；不能将仅有选择器或固定轮流发言视为已完成。

## 2. 已有能力与已核实缺口

| 事项 | 当前代码 | 本次需要补齐 |
| --- | --- | --- |
| 模式 | `Mission.room_mode` 已有 `roundtable`；创建 API 和 runner 尚未使用 | 两个创建入口、持久化、调度、前端贯通 |
| 多人点名 | `speaker.py` 安排 5 次轮流发言加末位收束 | 独立的参与者集合、讨论阶段和总回合预算 |
| 人格 | PR #5 已去除固定岗位，支持按配置顺序发言 | 沿用，不再建立永久主持身份 |
| 上下文 | `runner.py` 在循环外调用 `build_conversation_context`，通常只拿到任务开始时的历史 | 每个回合开始前刷新；普通接话无需依赖模型主动调工具才能看到上一位发言 |
| 用户插话 | API 写 `interrupt` 事件；runner 单次消费；`read_context` 只取 messages | 插话进入共享对话，所有后续角色都能看到，展示不重复 |
| A2A | MCP `invoke_agent`、串行队列、`wait_reply` split-turn、去重、收尾冻结均已存在 | 保留工具协议；限制参与者和总预算；一致处理 in-process 与 stdio 两条执行路径 |
| 预算 | `max_turns` 当前约束 workflow cursor，A2A 另有预算 | 圆桌所有实际回合统一计数，预留总结，明确拒绝原因 |
| 控制 | 普通插话没有结构化“收尾／停止”动作 | 增加用户控制事件和可见的请求状态 |
| 并发与恢复 | 创建 mission 是独立写入再启动线程，没有会话级原子占用检查 | 同一会话仅一个活动讨论/任务；重启后不留下永久运行状态 |

代码核对入口：[runner](../../backend/app/orchestration/runner.py)、[context](../../backend/app/orchestration/context.py)、[MCPTools](../../backend/app/mcp/tools.py)、[mission API](../../backend/app/api/missions.py)、[conversation API](../../backend/app/api/conversations.py)、[App](../../frontend/src/App.tsx)。

## 3. 用户界面与范围

- 输入区可切换“任务”和“多人讨论”；老会话、老 API 默认仍为任务。
- 多人讨论显示三位角色的姓名和性格标签，默认全选，至少两位。参与者与模式在运行中锁定；下一次讨论可更改。
- 第一版不提供主持角色或正反方设置。开场按选择顺序；自然回应由人格与内容决定。
- 运行中显示“初次交流／继续讨论／正在收尾”，同时显示“已用 x / 最多 y 个回合（含总结）”。轮次不是费用上限；现有实际用量面板保留。
- 输入区运行中按钮为“发送插话”。可以指定一位已选角色先回应；该角色回应后，其他人仍能看到插话。
- 控制按钮为“本轮后收尾”和“本轮后停止”，请求成功后显示等待当前回合结束。第一版不承诺立即终止 CLI。
- 结束后提供“继续讨论”：在同一 conversation 创建新 mission，沿用参与者，可编辑议题；旧消息、旧预算和旧状态保留。不复活已完成 mission。
- 请求失败、已进入收尾或旧页面状态过期时保留用户输入，刷新状态并给出可操作提示。

运行中不允许临时加入第四个参与者，不做并行抢话、自动无限续聊、长期记忆、语音、多人类账号或后台长期挂起。讨论不自动转为代码维护任务，不因某个角色发言而生成文件。

## 4. 发言策略与有界预算

### 4.1 默认发言顺序

设参与者为 A、B、C，默认基础序列为 A→B→C→A→B→C→总结，共 7 回合；两人是 A→B→A→B→总结，共 5 回合。

前三次/前两次为初次交流，已经能回应前面刚发布的内容，不要求各自重复回答原问题。第二遍明确回应具体观点、补充依据或修改判断。允许同意和简短说明没有新增观点；不设“必须有分歧”规则，不用关键词或模型裁判判定讨论质量。

总结者默认选“最近一次成功发言者之后的下一位参与者”；没有成功发言时不生成模型总结。显式收尾请求可指定一位参与者总结。此规则不绑定角色名字。

### 4.2 预算定义

- 圆桌请求的 `max_turns` 默认 `2 * participant_count + 1`，范围从 `participant_count + 1` 到 12。
- 新增独立的服务端圆桌上限 `roundtable_max_turns`，默认 12；超过服务端上限返回 422。任务模式原有 `max_turns` 语义不改，避免默认 6 截断三人讨论。
- 最后一格始终预留给总结。用户插话回应、A2A callee、A2A resume、失败的实际回合都计入总数；同一个 turn 的 provider fallback 不重复计回合，但每个 attempt 仍有超时和实际消耗。
- 基础发言被 A2A 或插话占用预算时，可能提前结束第二遍交流；不额外补齐“每人两次”。正常交流阶段需保留尚未发言者各一次基础回合及总结的空间，额外邀请不能饿死其他参与者。用户主动收尾/停止可提前结束这项保障。面板显示实际次数，不能承诺固定每人两次。
- 不接纳会消耗总结保留格的新任务。`wait_reply` 接纳时需要同时预留 callee 与 sender resume 两格；嵌套请求必须保留所有已接纳的 resume 槽位。
- 正常达到预算进入收尾，不标为故障。A2A 请求预算不足应记录未接纳原因，由当前角色完成已有发言，不让一条无效邀请把整场讨论变为 BLOCKED。
- 同一回合开始前已接纳的多条插话按事件顺序合并为最新用户条件，显示本次实际回应目标；没有足够交流格的定向追问明确标为“留待总结”，不许承诺每个点名都会单独启动回合。
- 首版固定基础序列与显式邀请即可；语义去重、自动判断“讨论充分”、自适应增减回合后续再做。

### 4.3 A2A 与参与者边界

所有 Agent 邀请继续走 `invoke_agent`，不解析正文里的名字或 `@`。宿主发给 MCP 的有效 agent 集合收敛为当前圆桌参与者；假适配器与 stdio server 都检查同一约束。邀请场外角色返回明确错误，不能自动扩大团队。

保留现有 `fire_and_forget` 对已安排目标的去重规则：不追加重复回合，但待执行目标应能读取邀请人的消息和邀请 purpose。不能因去重把请求内容悄悄丢掉。`wait_reply` 仍是 callee→sender resume 的串行 split-turn，不改为阻塞 JSON-RPC。

接纳与预算判断需要宿主统一的 roundtable policy；不能只改 runner 的一条分支而让 stdio 和 in-process 行为分叉。工具成功应表示已接纳；若只能持久化请求等待裁定，工具结果必须明确说明待裁定，不声称目标已发言。实现前在 PR 里固定字段与兼容测试。

## 5. 共享上下文是第一优先级

建立同一个只读上下文投影，供每回合 prompt 与 MCP `read_context` 共用。以稳定的消息/事件 ID 定序，不能用角色名或秒级时间戳猜测关联。

每次发言前纳入：议题、参与者、当前阶段与剩余预算、最新用户条件、截至本回合开始时的正式发言、与本回合相关的邀请 purpose。只使用 `publish_message` 的正式消息，不把 stdout、partial、thought 或诊断 trace 当作其他角色已说的话。

新插话在同一事务中保存为 user Message，并写入带 message_id 的 interrupt 调度事件；前端只展示一次。历史事件-only 插话通过投影兼容读取，不做批量改写数据库。跨 mission 继续讨论也应包含这些插话。验证用 mission 与系统控制文案不混入普通对话上下文。

上下文有界：优先保留本轮议题、最新用户插话和被回应的消息，再按时间倒序补充近期发言；截断时保留消息 ID、作者和明确的截断标记。第一版采用确定性裁剪，不额外调用模型做记忆摘要。具体字符预算在 RT-1 实现中用 fixture 固定，并报告短/长对话的 prompt 对比，避免把每条回复仍裁成 220 字而丢掉关键条件。

关键断言：B 的 prompt 能看到 A 刚发布的区别性事实；C 的 prompt 和 `read_context` 能看到 B 回复前用户加入的新条件；继续讨论后，该条件仍可读取。单纯断言含有“请参考历史”不算通过。

## 6. 用户控制、收尾与异常

控制只来自浏览器明确操作，不把用户正文中的“暂停”“结束”当作宿主指令；Agent 的“到此为止”也不直接触发状态变化。

| 动作/事件 | 行为 |
| --- | --- |
| 发送插话 | 保存后显示待回应；下一回合优先处理，指定目标须属于参与者。目标有待执行基础回合时前移该回合，否则使用剩余交流格；不可覆盖掉另一位唯一的初次发言机会。所有后续角色共享内容 |
| 本轮后收尾 | 当前 turn 完成后清空待执行 A2A/resume 与普通交流，开始一个总结回合；取消原因写事件 |
| 本轮后停止 | 当前 turn 完成后结束，不再调用模型总结；保留已经发布的消息与实际消耗 |
| 同时有停止和收尾 | 停止优先；重复请求按 request_id 幂等 |
| 正在收尾时收到新插话 | 返回 409，保留输入并提示结束后继续；不悄悄丢弃或把它塞进已启动的总结 |
| 收尾前有多个已接纳插话 | 所有内容进入上下文；剩余格不足以逐一回应时，总结处理这些补充，并标记哪些未单独回应 |
| 一个角色所有 Provider 都失败 | 记录 turn 失败和未参与原因，终止本次自动讨论为 blocked；保留部分结果，不冒充已达成共识；可在新 mission 调整参与者/配置后重试 |
| 总结回合失败 | 保留已有讨论，显示“讨论已有内容，收尾失败”，状态 blocked，可新建总结请求 |
| 后端重启 | 遗留 active 圆桌标为 blocked（服务重启），不自动重跑可能有副作用的 turn；允许在原 conversation 新建继续讨论 |

阶段为 `opening → exchange → closing → ended`；提前停止可从 opening/exchange/closing 进入 ended。`finish_requested`/`stop_requested` 是等待当前回合结束的控制标记，不伪装成 CLI 已停止。

外层 Mission 状态复用 running/completed/blocked/failed；用 `discussion.end_reason` 区分预算结束、用户收尾、用户停止、Provider 失败和服务重启。用户停止是用户选择的正常结束，UI 不显示“已完成任务”的成功承诺。异常和正常截止不能混用。

API 接受控制、runner 领取下一回合和提交终态需要同一事务化状态检查，防止“已接受停止之后又领取下一回合”及“返回 202 的插话永久无人处理”。已领取的当前回合允许结束。正常终态前确认没有未处理控制；已接纳输入必须有消费或明确取消记录。重启/终态后来自旧 turn 的迟到 MCP 发布、产物和邀请必须被拒绝并记录，不能重新打开讨论；这一限制也要覆盖 stdio 子进程，不能只依赖内存队列。

## 7. 数据与 API 草案

以现有 Mission + conversation 为载体，不新建聊天室数据库或另一套消息传输。

创建 mission 的两个入口统一支持：

```json
{
  "goal": "Club 下一步应该优先做什么？",
  "room_mode": "roundtable",
  "discussion": {
    "participant_ids": ["chulan", "baobao", "wangye"],
    "max_turns": 7
  }
}
```

- `room_mode` 缺省为 task。只开放 task/roundtable；game 仍不开放。
- roundtable 的 participant_ids 有序、无重复、至少 2 位、首版最多 3 位，全部必须存在；拒绝空集合和未知 ID。
- roundtable 使用 discussion.participant_ids；与旧 target_agent_id(s) 同时传入返回 422，避免两个来源争夺人选。任务模式原有多 @ 行为保留；UI 不擅自把旧任务升级为圆桌。
- 请求配置在 mission 创建时固化；旧 JSON 没有 discussion 时保持兼容。运行状态从事件投影得到，不从浏览器 localStorage 推测。
- `POST /api/missions/{id}/discussion-controls` 接收 `{action: "finish" | "stop", request_id, summary_agent_id?}`；summary_agent_id 只用于 finish。归档/非圆桌/终态/非法目标返回明确错误；同一 request_id 重试先返回原处理结果。
- 原 interruptions 接口扩展 request_id 幂等，并为 roundtable 校验阶段、参与者及活动状态。
- `build_mission_detail` 和 SSE 统一添加可空 discussion 视图：参与者、phase、turns_used、turns_limit、当前 speaker、pending_control、end_reason、总结 message_ids 与未回应插话 IDs。
- 事件建议包括 discussion.started、phase_changed、control_requested、control_handled、ended；继续复用 turn、message、interrupt、a2a 事件，通过 ID 关联。不要把总结内容复制到多个来源；总结是普通正式消息，视图只引用其 message_ids。

所有入口在同一仓库事务中检查 conversation 是否归档、是否已有活动 mission，并创建 mission + 首条用户消息。并发双请求只有一个成功，另一个 409；不能仅靠前端 busy 状态。新建 mission 的幂等 request_id 应避免网络重试创建两次讨论。归档活动会话返回 409，用户先停止并等终态。

浏览器的用户控制 HTTP API 属于用户↔宿主路径。Agent↔宿主仍只使用现有 stdio MCP；任何新增 Agent 能力都必须扩展 MCP，不能让 Agent 调这些用户控制端点。

## 8. 实施拆分

按顺序交付三个短 PR，前一个合入后再从最新 main 创建下一个分支。所有 PR 标注真实协作者，Codex 实质参与的 commit 带 `Co-authored-by: Codex <noreply@openai.com>`，Squash 保留署名。

### RT-1：共享对话与插话可靠性

范围：`orchestration/context.py`、runner 上下文刷新、`mcp/tools.py::read_context`、`api/missions.py` 插话写入、`api/mission_detail.py` 去重、Repository 事务方法与相关测试。

先补能复现的测试：同 mission A→B 可见性、插话后 B/C 均可见、跨 mission 可见、API/SSE 不重复、历史事件-only 插话兼容、过滤验证/partial/trace、长消息裁剪顺序。再实现统一投影和事务写入。保持现有任务模式可用；不提前展示多人讨论开关。

完成条件：上述测试和现有全量回归通过；提供 prompt 前后对比；无需模型调用也能证明传入上下文正确。

### RT-2：圆桌后端闭环

范围：`domain/models.py`、`settings.py`、`storage/repository.py`、两个创建 API、控制 API、`orchestration/roundtable.py`（新增策略）、runner 的 roundtable 分支、事件与 mission detail；必要的 MCPTools/server/adapter policy 参数透传。

先测创建验证和旧数据默认值，再测实际回合预算（含失败、fallback、callee/resume 预留）、参与者限制、A2A 去重但不丢 purpose、收尾/停止优先级、控制幂等、事务竞争、终态插话拒绝、会话活动锁、重启恢复。in-process 与 stdio 必须有同一组契约用例。任务模式既有路由不改。

新增 `scripts/smoke_roundtable.py --fake`，使用有脚本行为的 fake adapter 验证“观点→回应→插话→修正→总结”；不能仅让 echo prompt 返回成功。提供 `--real` 入口但默认不调用真实模型，复用临时 provider 配置。

完成条件：通过 API 能完整发起、插话、收尾/停止和读取终态；预算任何路径不能越界；已有 iron laws 与 A2A 回归全绿。

### RT-3：界面与体验验收

范围：`frontend/src/types.ts`、`lib/api.ts`、`lib/missionView.ts`、`App.tsx`、`MissionComposer.tsx`、`MissionRoomPage.tsx`、必要的讨论控制组件与对应样式/tests；README 与使用说明。

先测模式默认值、选人验证、运行中锁定配置、单人点名插话、显示控制请求已接收但未停止、409 时保留输入、SSE 刷新状态、完成后新 mission 继续、归档只读和历史任务兼容，再实现 UI。

完成条件：三人/两人、正常预算结束、提前收尾、停止、Provider 失败、刷新后恢复显示、继续讨论七条用户路径均可走通。UI 中不出现固定岗位，也不显示无法计算的精确费用或完成时间。

## 9. 验收标准

必须满足的产品场景：

1. 用户提出“Club 下一步该做什么”，三位角色的观点能够互相引用；不是三份独立答案拼接。
2. 中途补充“只有一周开发时间”，后续两位及总结均能看到并回应这一条件；刷新后插话仍存在且只显示一次。
3. 点名王也先回答，不改变参与者集合，也不让王也永久负责某类工作。
4. 用户要求收尾后只启动一个总结 turn；结论区分共识、分歧、未知，不替用户做未授权执行。
5. 用户要求停止后不启动新的普通回合或总结；已发布内容保留，状态文案准确。
6. 反复邀请、嵌套 wait_reply、频繁插话、fallback 均无法突破总回合上限；拒绝或取消都有可追踪原因。
7. 同时发送两次开始请求、最后一轮期间插话、后端重启均有确定结果，无重复任务或永久 running。

实现完成后的命令门槛（未来执行，不表示本规划已通过实现验收）：

```bash
python -m pytest backend/tests -q
(cd frontend && npm test && npm run build)
python scripts/smoke_onboarding.py --fresh --fake
python scripts/smoke_conversation_flow.py --fake
python scripts/smoke_real_cli_mission.py --fake
python scripts/smoke_roundtable.py --fake
python scripts/privacy-check.py
git diff --check
```

真实体验验收单独记录：实际 Provider、每次请求耗时、可用 usage、议题、插话内容、最终结果和人格观察。未跑真实模型不能标记真实对话已验收。各 CLI 流式能力不同，UI 必须支持“正在发言但暂无 partial”，不伪造输出。

## 10. 评审关注点与后续

- 自动交流优先是当前建议；若用户选择“每人一次后等我”，在实现前调整截止策略，不叠加第二套长驻会话引擎。
- 第一版选择回合边界生效的停止。立即打断需后续处理子进程取消、工具副作用、消息完整性与重启恢复，不能只隐藏 UI。
- 固定短讨论不保证每次有争议，也不保证模型遵从全部人格细节；首版用真实对话样例验收质量，不构造虚假的“共识分数”。
- 优先解决新鲜上下文、预算与控制可靠性，再考虑动态主持、自动跳过发言、长期记忆和更大团队。

执行记录：本文件仅完成代码现状核对与规划；RT-1 / RT-2 / RT-3 均未开始实现。

协作者：Buaixihongshi 提出产品方向并确定人格原则；Codex 负责代码核对、方案拆分和本文编写。默认推进节奏及其他提案细节尚待产品评审。
