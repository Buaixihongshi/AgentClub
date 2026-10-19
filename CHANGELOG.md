# Changelog

本项目的重要变更记录在此文件中。

## [Unreleased]

### Added

- 新增可选的 Antigravity 常驻进程模式：按会话与角色隔离、每轮执行凭证、正式回复去重、宿主退出监护及中断恢复记录；默认关闭，详见 [2026-10-18 升级记录](docs/upgrades/2026-10-18-antigravity-session-recovery.md)。
- 新增代码仓库维护内核：受信仓库登记、隔离 Git worktree、无 shell 验证契约、持久化维护状态、重启恢复、审批/反馈记录与受控进化提案。

### Changed

- 三位角色改为通透机敏、坦率好奇、松弛笃定的讨论伙伴：移除固定工种路由，统一能力边界，更新人格提示、新用户介绍与界面标签。

- 将用户可见的产品名称统一为 AgentClub。
- README 增加首次启动、CLI 接入和团队配置演示图。
- README 按产品定义、核心能力和差异化优势重构叙事，并重点说明角色、Provider 与路由三层解耦。
- 新增完整英文版 `README_EN.md`，并在中英文文档之间提供语言切换入口。
- A2A 接力增加可配置的无进展软超时、结构化活动续期和 Provider fallback；前端按目标轮次真实终态展示接力，不再将已阻塞任务标记为等待中。

### Fixed

- 常驻 Antigravity 的迟到 MCP 调用与重复发布受到执行凭证和操作回执约束；工作进程异常退出时核对已保存回复并保留进度，避免整轮盲目重放。
- 将任务复杂度与文件交付意图解耦：讨论、比较和评审任务不再因为宝儿姐参与而自动生成产物，演示模式与真实 Provider 使用一致的按需交付边界。

## [0.1.0] - 2026-08-05

### Added

- 新用户一键启动和首次对话引导：先通过内置演示模式成功触发张楚岚，再进入真实 CLI。
- Codex-first 接入流程，包括安装/认证提示、MCP 真实对话验证和三角色一键共用 Codex。
- Claude Code 与 Antigravity CLI 的后续扩展入口及常驻团队配置。
- 会话归档、已归档列表和恢复能力。
- 健康检查、onboarding API、Provider 诊断与设置持久化迁移。
- 设置页分类导航、Provider 生命周期状态和角色路由管理。
- `scripts/start.sh` 一键启动与 `scripts/release-check.sh` 发布门禁。

### Changed

- 团队角色保持张楚岚 / 冯宝宝 / 王也，Provider 与角色人格解耦。
- 设置入口统一进入 Header，团队配置并入设置页；移除对话顶部 Agent 参与状态条。
- 真实 MCP turn 只接受 `publish_message` 作为权威角色消息，CLI stdout 降级为调试信息。
- 默认提交配置保持真实 Provider 关闭，避免首次启动产生模型费用。
- 前端构建链升级到 Vite 8、React 插件 6、Vitest 4 和 TypeScript 5.9，清除发布检查发现的 npm high/critical 漏洞；最低 Node.js 版本调整为 `20.19+` 或 `22.12+`。

### Removed

- 从提交配置、角色 fallback、onboarding、Provider 状态和 smoke 路径中移除 Gemini CLI。
- 从用户界面移除 Fake CLI 命名，内部适配器统一展示为“演示模式”。

### Safety

- Codex 和 Claude MCP 配置使用临时隔离目录，不写入用户全局配置。
- Antigravity 全局配置例外保留锁、sentinel 恢复和精确还原语义。
- 归档会话只读且不删除历史数据；恢复后方可继续创建任务。
- 项目采用 MIT License，并提供无 Git 历史的白名单发布导出与隐私检查流程。
