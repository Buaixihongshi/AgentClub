# 自适应自主工作验收记录（历史阶段）

本记录保留当时入口、代码基线和已知阻断，不代表最终界面或本次 PR 的当前检查结果；最新情况见 [2026-10-24 升级验收](2026-10-24-conversation-first-autonomy.md)。

日期：2026-10-23。依据：[用户已确认的 Spec](../plans/2026-10-23-adaptive-agent-autonomy-spec.md)。状态：首版已实现，协议、离线回归与浏览器场景验收通过；依赖审计仍有既有阻断，未发布。

本记录区分协议、实际终端执行和真实模型能力。自动化使用可控 Worker 验证状态与交接；浏览器场景也使用脚本 Worker，并生成真实文件供实际交互。未将脚本结果声明为 Codex/Claude/Antigravity 模型的端到端开发验收，未执行真实生产发布。

## 最终检查结果

| 检查 | 结果 |
| --- | --- |
| 后端全量测试 | 948 通过，1 跳过 |
| 前端全量测试 | 38 个文件、308 项测试通过 |
| 前端生产构建 | TypeScript 与 Vite 构建通过 |
| 离线冒烟 | 首次引导、会话连续性、多 Agent mission、圆桌均通过 |
| 离线评测 fixture | 通过 |
| Python 依赖一致性 | `pip check` 通过 |
| 导出隐私检查 | 干净导出快照通过，未复制本地数据库、凭证或 QA 产物 |
| 补丁与文档 | `git diff --check`、文档相对链接及代码块检查通过 |
| 前端依赖审计 | 未通过：既有 5 项漏洞（2 moderate、3 high），涉及 Vite/Vitest 等；本轮未改动依赖锁文件 |

以上不等于发布门禁全部通过。依赖审计阻断及真实环境验收未完成时，不标为已发布或全套 release-check 通过。

## 验收项覆盖

本轮代码未提交，不能用基础 HEAD 冒充已验收实现版本。版本锚点如下：

- 基础提交：`7f92ffdbdb01c569ea75d5da98b3bec8b7521537`。
- 实现内容清单 SHA-256：`df8ec49f90cd38f682d5f4c81b988ca0448bfa7fe1dfd6b86d9a04076a8c22aa`。
- 本次 Spec 文件 SHA-256：`18892daaba298520a5d5a492db32b826392ef040ae31ea09436ad7efc009efe5`。
- 内容清单覆盖 `backend/`、`frontend/`、`config/`、`scripts/`、`.github/`、`.gitignore` 与 `requirements.txt`，按相对路径和文件 SHA-256 排序计算；完整清单保存在忽略目录 `output/playwright/adaptive-work-qa/code-manifest.json`。

| Spec | 验证入口与结果范围 |
| --- | --- |
| AC-01 单 Agent 短路径 | `test_single_agent_minimal_delivery_persists_dirty_content`：一位角色、无设计/强制审核、保存产物与核对依据后交付 |
| AC-02 授权与文档终点 | `test_original_scope_cannot_be_expanded_by_agent`、`test_api_intake_document_and_snapshot_scope`、`test_scope_is_enforced_by_os_sandbox`：API 任务只交文档，不能提升执行范围，真实 OS 拒绝文档任务写源代码 |
| AC-03 按需设计 | `test_design_decision_version_and_duplicate_acceptance`；浏览器实际显示三版方案，选 B 后自动接续。简单任务没有设计节点 |
| AC-04 条件式独立审核 | `test_high_risk_review_and_stale_version_cannot_approve`、`test_host_risk_floor_detects_auth_changes_even_if_author_declares_local`、`test_review_has_no_host_write_or_lifecycle_escape` |
| AC-05 持久责任与交接 | `test_delegation_keeps_parent_accountable_and_integration_required`、`test_transfer_waits_for_target_acceptance_without_human_handoff`：协作不解除父责任，目标角色明确接收后才转移负责人 |
| AC-06 页面关闭与恢复 | 持久化测试和浏览器刷新、重启测试服务后重新打开同一会话；选定设计及最终待审批状态保留 |
| AC-07 动态分工与依赖 | 协作/集成测试、责任祖先循环拒绝；范围变更须走用户决定或用户控制接口 |
| AC-08 版本证据 | 旧审核不能覆盖新内容、需求修订保留历史交付、待选设计变化则旧选择失效、根需求修订使子任务待决定卡失效 |
| AC-09 并发与幂等 | `test_atomic_claim_and_pause_continue`、`test_strict_contract_and_idempotent_human_commands`、`test_live_orphan_process_prevents_recovery_writes`，以及旧协议/执行恢复回归 |
| AC-10 安全恢复 | 无变化时安全接续，已改变文件先只读核对；实际状态不明的发布仅查询、不重放；旧进程可能仍写入时保持等待 |
| AC-11 整体验收 | `test_runner_mcp_delegation_and_integration_flow`：子任务完成后重新唤起原负责人，根任务必须另行提交整体验收结果 |
| AC-12 发布批准 | `test_configured_release_executes_only_approved_candidate_and_queries_actual_state`、`test_release_rejection_allows_new_candidate_and_new_approval`：无批准不执行，指定环境与快照，查询实际结果后完成 |
| AC-13 累计预算 | `test_no_structured_result_and_root_budget_do_not_fake_completion`、协作流程根累计次数；Provider 失败与子任务不新开免费预算 |
| AC-14 用户控制 | 暂停允许当前回合收尾并阻止后续领取，暂停跨恢复保留；取消不宣称撤销已有副作用 |
| AC-15 有据完成 | 无结构化结果不交付、真实失败退出码与完整日志保留；默认 Fake 明确阻塞，不伪造产物或审核 |
| AC-16 人类决定 | 明确单卡聊天回复可记录，多卡/模糊回复不批准；重复决定幂等，旧版本选择不直接生效 |
| AC-17 发布配置边界 | 缺少发布适配器时保留已批准版本并等待；开发交付和上线交付终点分别测试 |
| AC-18 兼容性 | 保留旧共享任务测试、圆桌与讨论测试、MCP 测试和铁律测试；新功能不改变真实 Provider 默认关闭设置 |

主要自动化入口：[后端场景](../../backend/tests/test_adaptive_work.py)、[前端决定卡](../../frontend/src/tests/AdaptiveWorkBody.test.tsx)、[旧协议回归](../../backend/tests/test_shared_work.py)。

## 浏览器实际检查

通过 Playwright CLI，在独立临时项目和数据库中检查：

1. 选择「开发并申请上线」，从候选菜单选择张楚岚，提交需求。
2. 原负责人自动分派王也，三张不同的 HTML 方案在页面中可见。
3. 刷新并重新打开会话，工作、负责人及待选择卡保留。
4. 选择 B 并填写反馈后，后台自动接续设计确认、宝宝实现、负责人集成验收和王也审核；根任务进入待上线批准，未提前显示已上线。
5. 窄屏（390×844）中对已保存 HTML 预览实际输入并提交，观察到「报名成功！」和禁用按钮；该预览仅说明本地交互可操作，不声明连接业务后端。
6. 重启测试服务后待审批记录仍保留，页头显示「等待你的决定」，不会把一次 mission 结束当成需求完成。
7. 批准具体候选后，未配置发布入口时明确等待配置，不执行生产发布。

本地证据保存在被忽略的 `output/playwright/adaptive-work-qa/`：`ui-options-desktop.png`、`approval-mobile.png`，以及隔离 QA 数据库和脚本。数据库不作为仓库交付物提交。

## 运行限制

- 原生 CLI 隔离首版依赖 macOS `sandbox-exec`；纯文本 API 不会被伪装成可开发的工具连接。
- 默认 Fake Provider 保持不执行；实际使用须配置真实连接。各真实 CLI 的本机连通性、模型输出质量和目标项目开发效果需要按实际环境继续验收。
- 同一工作树的动作保守串行；尚未引入多工作树并行写入或通用分支合并。
- 发布依赖用户配置的受管入口及准确的状态查询；没有通用的外部副作用“严格只执行一次”保证。
- 运行时同时检查权限、状态、来源与版本；目标语义是否实现仍依赖真实观察和适用的独立审核，不能由字段齐全自动证明。
