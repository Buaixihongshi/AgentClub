# AgentClub 协作入口

- 用中文回复。先读任务涉及的代码；流程见 `docs/SOP.md`，环境见 `docs/ENVIRONMENTS.md`，发布见 `docs/RELEASING.md`。
- 操作环境前运行 `./scripts/env.sh status`。一项任务一个分支/worktree；使用 `env.sh dev <task>`，不修改正在服务用户的 checkout。
- 功能分支在自身环境自验，合入 main 后在 alpha 验收。alpha 只能是干净的主线镜像，不能用日常 runtime 代替测试。
- 端口及热更新规则以 `config/environments.json` 为准；检查命令以 `scripts/checks.py` 为准。旧脚本是兼容入口，不另建一套启动或门禁流程。
- 开发时按影响运行检查分组；交付时记录实际 SHA、源码摘要、检查结果和预览入口。检查失败、跳过或未验证的内容如实说明。
- 新行为和 bug 补可观察回归；共享调度、数据、安全、协议及门禁变更在合并前需要非作者审阅。不得把作者自检或测试替身写成独立审查、人工批准或真实模型验收。
- 不上传数据库、凭据、完整本机路径、私有检查报告或备份。保留未提交工作；不批量清理旧 worktree，不 reset 运行目录。
- 遵守 `CONTRIBUTING.md` 的协作者与提交署名要求。合并依据当前 head 的必需 CI、适用审阅和 GitHub 分支规则；不再要求 Owner approval 或 Owner release。
- Agent 可准备候选、执行离线检查、创建/更新 PR；不得伪造审阅或批准、自行合并或启用自动合并。
- 实际日常数据导入、激活、回滚及故障恢复由所有者明确执行；可以在临时 fixture 中验证这些路径，不触碰日常实例。
- development/test 默认使用演示/fixture；alpha 启动时继承日常有效 CLI 配置，任务数据独立。真实验收遵循用户授权，不擅自将所有角色统一成 Codex。合并不等于部署授权。
- 浏览器操作优先使用 ego-browser，先运行 `ego-browser skill` 读取本机版本指引。
