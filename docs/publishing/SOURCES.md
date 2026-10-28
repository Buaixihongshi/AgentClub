# 第二版资料核对与证据说明

整理日期：2026-10-26。功能与实现核对基线：`d7c11eacf55f79b8c9eabfa1f509c15b95c1c5de`。本文件解释内容选择与证据口径，不是一次新的软件发布验收报告。

## 遍历方法与范围

对 FunProject 中 Markdown、MDX、RST、TXT 文本进行目录遍历和项目名称检索，覆盖 AgentClub、YiRen Hub 与早期异人原型。跳过依赖、构建缓存、数据库、产物、运行日志、隐藏状态和私有恢复归档，不将它们作为发布材料。

在创建本次文档工作区之前，匹配到 **1,615 份文本文件，按内容 SHA-256 去重为 148 份**。包含 README、设计、事故、验收、计划、工作区约定、少量依赖说明与验收样例；不是 1,615 篇独立技术文档。遍历与哈希检索覆盖全部匹配文本，人工式内容核对重点放在当前文档、历史事故及有差异的设计/验收材料，不将目录命中冒充逐篇精读。

| 资料来源 | 用途与优先级 |
| --- | --- |
| 工作区根索引与约定 | 确认项目位置、Git 边界、历史归档和私有恢复资料边界 |
| 当前受管发布快照与 alpha | 57 份基线文本，含 10 份事故记录；结合实际代码判定当前能力 |
| 原 yiren-hub 工作区 | 核对较早 README、未提交方案和历史真实 CLI 验收，不能代表最新日常源码 |
| features 与历次 releases | 去重并对照功能演进；未合入分支只作为候选材料 |
| 环境 history、plans | 近期超时、发布切换排查、功能同步与待实现观测方案 |
| 三个早期原型与旧独立 release | 解释项目来源；不将原型的 LangGraph、七角色或旧 CLI 协议并入当前能力 |

同名文档冲突时，优先读取基线实现与当前操作说明。计划只说明设计意图，历史验收只证明当时发生的结果；目前代码存在不自动证明当前环境已完成真实模型验收。

完整路径和哈希清单保存在本机私有整理记录中，不提交个人目录、原始日志、数据库或配置。公开文稿仅保留仓库内相对路径与代码基线。

## README 语气与结构参考

根据用户指定，读取[《2024 年末随记》](https://buaixihongshi.github.io/posts/2024%E5%B9%B4%E6%9C%AB%E9%9A%8F%E8%AE%B0/)，参考其从具体情境展开、简短段落和克制自然的第一人称表达；读取 [Bothouse 中文 README](https://github.com/zuiho-kai/bothouse/blob/main/README.zh-CN.md)，参考其先说明用户能做什么，再介绍用法、特性、上手和文档的顺序。

正文重新创作，不搬用个人经历或对方项目承诺。Unity 的功能仍以本项目实现为准，尤其不引入对方的多任务并行、跨平台频道和夜间自动复盘能力。本轮只调整 README 及文档包中的编辑说明，面试与博客稿保持原内容。

## 文档包功能核对矩阵

| 功能内容（首页择要介绍） | 操作/设计依据 | 实现核对入口 |
| --- | --- | --- |
| 角色、主用/备用、独立模型 | [人格](../PERSONALITIES.md) | `frontend/src/components/SettingsModal.tsx`、`backend/app/adapters/provider_router.py` |
| CLI 检测、认证、真实验证与演示 | 原 README、当前引导与 Provider API | `OnboardingCard.tsx`、`backend/app/api/onboarding.py`、`api/providers.py` |
| API 三种协议、目录与文本能力 | 当前设置界面 | `storage/api_connections.py`、`adapters/api_catalog.py`、`adapters/api_text.py` |
| 连续会话、@ 候选、归档恢复、SSE | [共享上下文](../SHARED_CONTEXT.md) | `MissionComposer.tsx`、`lib/inputConfirmations.ts`、`api/conversations.py` |
| `/discuss` 与六阶段隔离讨论 | [讨论技能](../ROUNDTABLE.md) | `skills/discuss/SKILL.md`、`orchestration/deliberation.py`、`adapters/deliberation_cli.py` |
| 提议、确认、持久责任与累计预算 | [自主工作](../AUTONOMOUS_WORK.md) | `work/adaptive.py`、`work/decisions.py`、`work/runtime.py` |
| 独立任务目录与受管预览 | [任务工作区计划](../plans/2026-10-26-task-workspaces.md)，并与当前操作说明交叉核对 | `work/environments.py`、`frontend/src/components/WorkEnvironment.tsx` |
| 内容快照、审核与上线核对 | [自主工作](../AUTONOMOUS_WORK.md) | `work/snapshots.py`、`work/validation.py`、`work/releases.py` |
| 记忆、检查点、索引与模型交接 | [上下文记忆](../CONTEXT_MEMORY.md) | `memory/store.py`、`assembly.py`、`checkpoints.py`、`handover.py`、`MemoryPanel.tsx` |
| 文件直接导入与独立卡片 | [文件交付事故](../incidents/2026-10-20-wangye-demo-timeout.md) | `mcp/tools.py`、`lib/conversationTimeline.ts`、`tests/artifactDelivery.test.tsx` |
| 全局观测、规则检查与成本 | [观测](../OBSERVABILITY.md) | `observability/`、`ObservabilityOverview.tsx` |
| A/B/C/D、真实 A2A 与编码试点边界 | [评测](../EVALUATION.md)及三个 `eval/pilots/*/README.md` | `evaluation/`、`scripts/run_eval.py` |
| 仓库维护基础 | CHANGELOG 及维护入口 | `maintenance/service.py`、`api/maintenance.py`、`MaintenanceButton.tsx` |
| 环境身份、检查、发布与恢复 | [环境](../ENVIRONMENTS.md)、[发布](../RELEASING.md)、[SOP](../SOP.md) | `runtime_identity.py`、`scripts/environment_cli.py`、`checks.py`、`releases.py` |

表中简写后端路径位于 `backend/app/`，前端组件位于 `frontend/src/components/`。它们用于定位代码，不是新增公开 API 承诺。

## 三个事故的筛选逻辑

选择同时满足“问题具体、根因有证据、方案有取舍、结果可检查”的案例，并覆盖不同工程层次。

1. **真实 CLI 隔离启动失败**：跨三个 Provider 的差异明确，有对照诊断和真实六阶段闭环，适合展示系统集成与验收方法。
2. **HTML 交付超时**：有工具耗时、生成耗时、文件差异，后续导入与卡片方案完成可见交付，适合展示性能与产品接口设计。
3. **提交/续租竞态**：故障是交付前回归发现，根因与代码可直接对应；已有确定性测试，适合展示事务、状态机和权限判断。

其他材料的处理：

- 10-16 跨任务交接与超时、10-17 历史失败不可见、10-18 常驻进程恢复，都有价值，但与前三项的边界、诊断和交付主题部分重复，保留为追问备选。
- 10-20 休眠与活动显示事故中，活动展示已修复，休眠预算问题不能算作已修复成果。
- 10-24 alpha 路由一致性体现环境与真实验收的重要性，已吸收进 README 的环境说明；单 Provider 连通性不写成完整团队交付。
- 10-26 长回合超时记录包含实际设置缓解，但自动进入持久工作和预算机制仍有遗留，不能写成根治成功。
- 10-26 发布切换排查有应急恢复与分支加固记录；尚不能将分支修复写成当前基线已发布能力。该记录对 Socket 释放的说明也需额外验证，不能仅凭端口仍占用断言 TIME_WAIT 是根因。
- 观测语义评审新方案仍是候选工作，不将其 Judge 流程写进当前已实现功能。

## 历史数字的引用规则

| 文稿数字 | 来源 | 解释限制 |
| --- | --- | --- |
| 六回合、六提交、六尝试、零重试 | [讨论启动 STAR](../incidents/2026-10-20-deliberation-v2-startup-star.md) | 固定连通性题，无分歧且复核无发现；不是全部讨论质量 |
| 58 次原生调用、累计约 2.554 秒 | [demo 事故](../incidents/2026-10-20-wangye-demo-timeout.md) | 事件中的工具执行时间，不是完整模型或网络耗时 |
| 约 71.26 秒、12,748 output tokens | 同上 | 修复前单次大文件重输，不是已实测的净节省 |
| 832 后端通过、1 跳过；234 前端通过 | 同上后续实施 | 当时回归，不能用于宣称当前版本全量检查通过 |
| 1011 通过、1 失败、1 跳过 | [自主任务升级复盘](../incidents/2026-10-24-conversation-first-autonomy-upgrade.md) | 故障发现时的首次全量结果；不是修复后成绩 |

当前角色与模型设置不能用于改写历史参与者。当前配置不等于事故时配置；CLI stdout、模型自述、工具成功、产物保存、任务完成与实际上线都需分开。

## 本次文档验证

本次在独立文档工作区核对内容，不启动开发/alpha/日常服务，不运行付费模型，也不执行历史任务重放。

已实际执行 3 项针对性回归：

- `test_host_can_renew_only_its_current_discussion_capability`
- `test_submission_winning_renewal_race_is_terminal_and_does_not_extend_lease`
- `test_write_artifact_imports_from_host_workspace_via_stdio`

结果 **3 passed**；有一条已有 Starlette/AnyIO 弃用提示。使用已有 Python 环境的依赖，测试代码来自本次工作区。该结果验证两个案例的关键实现与边界，不替代全量回归、真实 CLI 或浏览器验收。

五份文档的相对链接、代码块与逐行空白已核对，统一 `check.sh hygiene` 和 `privacy-check.py --git` 通过；隐私扫描覆盖 tracked 与非忽略候选文件。未执行无关全量模型/依赖性能评测；英文、全新截图、远端 Release 和博客发布不包含在本次写作结果中。

## 项目更名

2026-10-27，项目对外名称由 AgentClub 调整为 **Unity**。改名候选基于主线 `ab5e615d34969839b74ac0e130475e2f931d6d73`，并带入上一轮文档工作区的最新内容。本次 README、英文介绍、面试与博客文稿，以及当前界面和引导使用新名称；原始事故、验收数字、固定提交引用保留当时事实。资料遍历发生于改名前，因此上文的检索名称仍为旧称。

产品改名阶段保留了旧仓库地址；用户随后授权将 GitHub 仓库改名为 `Buaixihongshi/Unity`，已于 2026-10-27 完成。仓库 ID 与主分支提交保持一致，本地共享 Git 配置中的 origin 和兼容远端地址已更新，README 及本次发布稿使用新链接。数据目录、环境变量、MCP 服务名与管理协议头继续兼容，本次不迁移数据库、不切换日常实例。

改名验证：前端 41 个文件、338 项测试及类型/构建通过；后端引导、MCP 和运行控制相关 35 项通过；四条离线 smoke 通过。独立 development 页面实测标题、页头、欢迎会话及演示回复均为 Unity，未调用真实模型。英文旧截图明确注明改名前的品牌。

实际协作：用户确定新名称；Codex 主代理整理文档、后端及脚本文案并完成集成验证，前端代理处理界面、包名与前端检查，另一代理只读核对兼容边界和最终差异。复核发现的英文冠词及历史截图标注问题已修正；这不是人工发布批准。
