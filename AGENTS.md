# AgentClub 协作入口

- 用中文回复。先读任务涉及的代码；流程见 `docs/SOP.md`，环境见 `docs/ENVIRONMENTS.md`，发布见 `docs/RELEASING.md`。
- 操作环境前运行 `./scripts/env.sh status`。一项任务一个分支/worktree；使用 `env.sh dev <task>`，不修改正在服务用户的 checkout。
- 功能分支在自身环境自验，合入 main 后在 alpha 验收。alpha 只能是干净的主线镜像，不能用日常 runtime 代替测试。
- 端口及热更新规则以 `config/environments.json` 为准；检查命令以 `scripts/checks.py` 为准。旧脚本是兼容入口，不另建一套启动或门禁流程。
- 开发时按影响运行检查分组；交付时记录实际 SHA、源码摘要、检查结果和预览入口。检查失败、跳过或未验证的内容如实说明。
- 新行为和 bug 补可观察回归；共享调度、数据、安全、协议及门禁变更在合并前需要非作者审阅。不得把作者自检或测试替身写成独立审查、人工批准或真实模型验收。
- 不上传数据库、凭据、完整本机路径、私有检查报告或备份。保留未提交工作；不批量清理旧 worktree，不 reset 运行目录。
- 遵守 `CONTRIBUTING.md` 的协作者与提交署名要求。Owner approval 的真实来源是 GitHub 当前 head 的状态，不是 PR 正文或本地 JSON。
- Agent 可准备候选、执行离线检查、创建/更新 PR；不得代所有者运行 Owner release、写批准状态、自行合并或启用自动合并。
- 实际日常数据导入、激活、回滚及故障恢复由所有者明确执行；可以在临时 fixture 中验证这些路径，不触碰日常实例。
- 真实 CLI 检查需用户明确选择；默认使用演示/fixture。合并不等于部署授权。
