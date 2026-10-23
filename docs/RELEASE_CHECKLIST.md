# AgentClub 0.1.0 发布清单

## 已完成

- [x] 前后端版本统一为 `0.1.0`
- [x] 完整后端测试通过（422）
- [x] 完整前端测试通过（171）
- [x] TypeScript 检查与 production build 通过
- [x] Onboarding / conversation / mission 离线 smoke 通过
- [x] 默认真实 Provider 全部禁用
- [x] Gemini CLI 从用户可选路径移除
- [x] 一键启动脚本和发布检查脚本就绪
- [x] README、Acceptance、Handoff 与 Changelog 已更新
- [x] `.trellis/`、`output/`、`design-qa.md` 等本地产物不会进入发布提交
- [x] `pip check` 通过，`npm audit` 为 0 vulnerabilities
- [x] 一键启动在备用端口完成健康检查，退出后子进程与端口均已清理
- [x] MIT License 已写入仓库根目录和前端 package metadata
- [x] 白名单发布导出脚本与隐私检查脚本就绪

## 发布负责人确认

- [ ] 如本次对外宣称真实 CLI 可用，显式运行 `./scripts/release-check.sh --real-cli`
- [x] 公开分发许可证确认为 MIT
- [ ] 审查最终 `git diff`，只暂存本次发布所需文件
- [ ] 创建发布提交
- [ ] 创建 `v0.1.0` 标签
- [ ] 推送分支和标签，并粘贴 `CHANGELOG.md` 中的 `0.1.0` 内容作为发布说明

## 回滚边界

- 发布前不要把 `config/cli-providers.json` 中真实 Provider 改为默认启用。
- 不要提交 `data/*.sqlite3`、`artifacts/`、`.yiren/` 或 Provider 临时 MCP 配置。
- 如果真实 CLI 验证失败，保留演示模式并记录诊断；不要用 stdout 合成角色消息。


## 后续版本通用流程

上面的数字与勾选是 0.1.0 历史记录，不能作为后续提交的测试证据。每次发布重新核对：

- [ ] prepare 读取准确 PR head；真实 CI 与所有者放行覆盖该 head。
- [ ] 已合并到 main，并在 alpha 对准确合并结果核对用户路径。
- [ ] stage 建立 approved head → merge commit → 候选 SHA 的来源链和完整检查/产物校验。
- [ ] 首次迁移已由所有者停止旧服务、检查空闲并显式导入；未用空库替换旧数据。
- [ ] 所有者明确执行 activate，expected-current 与实际部署一致。
- [ ] 日常实例身份、current.json、前版及备份记录一致。
- [ ] 如需回滚，明确 --restore-state 的数据边界，并保留切换后的新状态备份。

命令与恢复细节以 [发布说明](RELEASING.md) 为准；检查实现以 scripts/checks.py 为准。
