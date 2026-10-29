# 聊天内轻量讨论

基线：origin/main ecada86。目标是把"多人讨论"从独立模式 + 六阶段默认流程，改成聊天里的轻量交互；
正式六阶段评审保留，改为用户主动升级。

## 1. 现状要点（已确认）

- 讨论入口是 `/discuss`（skill，状态条在输入框旁，内容进时间线），走 deliberation_v2；"多人讨论"模式开关
  在 ecada86 已不再挂载。`/discuss` 原先禁止 @成员。
- 协议：independent → exchange（最多 2～3 轮）→ awaiting_synthesizer（必须用户选）→ synthesis → review → finalizing。
  每批全部提交后才公开（`_publish` 写成普通聊天消息）。
- independent 结束时宿主已经用 `analyze_opinion_agreement` 按 decision_key 比对共识和冲突，不调用模型。
- 中途补充只能"带新议题重开"或"留到下次"（revise_input）。
- 实测：一个"一两句话"的问题，2 人讨论跑满流程 = 9 次调用、约 6 分钟、1527 字六节报告。

## 2. 新流程

```
/discuss [@成员…] 议题
  └─ 独立作答（并行、互不可见，全部完成后一起公开）       N 次调用
       └─ 分歧卡（宿主比对，不调用模型）
            ├─ 查证这个分歧 → 只请分歧相关的人做 1 轮 exchange → 回到分歧卡
            ├─ 让 X 综合     → synthesis（默认综合者已预选，可改）→ 结束（标注"未经复核"）
            ├─ 我来定        → 记录用户的决定 → 结束
            ├─ 正式评审      → 进入现有六阶段的剩余部分（exchange … review … finalizing）
            └─ 就到这里      → 以各自观点结束
```

- 默认只跑第一步：2 人 = 2 次调用。每张卡显示已用调用次数和用时。
- 分歧卡内容：一致项、分歧项（双方推荐 + 关键差异条件）、只有一方提到的判断项。
  明确标注"按判断项自动比对，措辞不同的分歧可能漏掉"；需要更准确时用"让 X 综合"。
- 用户的看法：用户在任一阶段发的消息作为"补充"，带入下一步的冻结材料（不再强制重开）。
  独立阶段进行中收到的补充，在独立阶段结束后才生效，不破坏同一批的独立性。
- 价值取舍（needs_human / decisions）仍必须由用户决定，规则不变。

## 3. 后端改动

- `DiscussionConfig` 增加 `depth: "quick" | "full"`。新建 `/discuss` 默认 quick；旧数据没有该字段按 full 处理，
  现有讨论行为不变。
- 状态机（`Deliberation.complete_batch`）：quick 下 independent 批次公开后进入 `awaiting_input`，
  `awaiting_reason = "choose_next"`（新），不自动进入 exchange / awaiting_synthesizer。
- 新控制动作（`/discussion-controls`，沿用 expected_revision 和 request_id 幂等）：
  - `verify_conflicts`：exchange 1 轮，只邀请分歧相关参与者，结束后回到 choose_next；
  - `synthesize`（agent_id 可省略，默认用分歧最少/首位参与者）：synthesis 后直接结束，结果标注未经复核；
  - `user_decide`（text）：记录为用户决定并结束；
  - `escalate`：depth 改为 full，从当前位置接着走现有流程；
  - 现有 `finish` / `stop` 保留，`finish` 在 choose_next 时表示"就到这里"。
- 补充输入：quick 下 `interrupt` 不再进入 revise_input，改为写入 `pending_inputs`，在下一步 `freeze` 时并入材料。
- `user_view` 增加 `choices`（当前可用动作）、`divergence`（一致/分歧/单方项，来自 agreement）、`budget`（已用调用数、用时）。
- `/discuss` 允许 @成员指定参与者（2～3 人）；不指定时沿用现有自动选择。
- 不改：独立性保护、批次公开、MCP 工具权限、沙箱、记录（PR 68）、V1 roundtable。

## 4. 前端改动

- 讨论只从 `/discuss` 进入；`/discuss @成员 议题` 把点名的 2～3 人作为参与者（1 人或超过 3 人时拦截提示）。
- 时间线：快速讨论的观点以角色消息显示（标注"独立观点"/"查证补充"，后端以 `discussion_visibility=opinion` 标记）；
  `DivergenceCard` 作为时间线末尾项，运行中显示每人进度，choose_next 时显示一致/分歧/单方项与动作按钮；综合结果是普通角色消息。
- 进度：独立阶段显示每人状态和用时（不显示内容）；`DiscussionSkillStatus` 保留为输入框旁的细状态条（停止）。
- 正式评审仍用现有 `DeliberationStatus` 的阶段视图，只在 depth=full 时出现。

## 5. 验证

- 后端：quick 状态机（独立后停在 choose_next、各动作的转移、预算计数、补充并入下一步、escalate 接续）、
  旧 full 讨论回归不变、控制动作的 revision 冲突与幂等。
- 前端：分歧卡渲染与按钮、模式开关移除后 `/discuss @成员` 的创建请求、旧 full 讨论仍显示阶段视图。
- 真实 CLI：development 环境跑 quick 全路径（独立 → 查证 → 综合、独立 → 我来定），记录调用数和用时。

## 6. 风险与待定

- 属于共享调度/协议变更，合并前需要非作者审阅。
- decision_key 比对依赖各自写的判断项，自由文本可能对不上；分歧卡要如实标注局限。可选改进：
  独立阶段提示词给出"建议判断项"（宿主从议题生成会调用模型，暂不做）。
- 已定：当前不再挂载模式开关，无需额外移除；"让 X 综合"后不默认复核（结束原因 `synthesized_unreviewed`），
  需要复核走"正式评审"；投票放到下一期。
- 讨论进行中（含等待选择下一步）输入框发送的内容作为补充并入下一步；要开始新话题需先点"就到这里"或停止讨论。
