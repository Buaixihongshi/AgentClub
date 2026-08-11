# AgentClub 0.1.0 发布候选验收

日期：2026-08-05

## 验收结论

AgentClub `0.1.0` 已达到本地发布候选标准：离线首次体验、Codex-first 真实 CLI 接入路径、多 Agent 会话、归档恢复、MCP 工具链和设置管理均已形成可运行闭环。

本轮没有自动调用真实模型。需要 CLI 账号、OAuth 状态或模型费用的验收被保留为显式发布门禁，不影响默认演示模式启动。

## 已验收能力

- 一键启动：`scripts/start.sh` 安装缺失依赖、启动前后端、等待健康检查并打开项目页。
- 新用户引导：无需模型账号即可先与张楚岚完成演示对话，再进入 Codex 安装、认证和 MCP 真实消息验证。
- 团队扩展：Codex 验证后可一键分配给三位角色；Claude Code 与 Antigravity CLI 作为后续可选扩展。
- 常驻团队配置：CLI 接入、角色 Provider/fallback 和运行参数统一收进设置页。
- 多 Agent 协作：张楚岚、冯宝宝、王也支持复杂度调度、定向 mention、A2A `invoke_agent` 和 `wait_reply`。
- MCP 权威消息：真实 provider 启用 MCP 时，只有 `publish_message` 才能形成角色发言；stdout 仅作为调试信息。
- 会话能力：连续任务保留上下文，会话可归档和恢复，归档状态不会删除历史数据。
- 可观测性：SSE、turn attempts、tool trace、artifact ownership、interrupt、usage 和阻塞摘要可在任务室查看。
- Provider 策略：Gemini CLI 已从提交配置和用户可选路由移除；Fake adapter 仅以“演示模式”对用户呈现。
- 配置安全：提交配置中的 Claude Code、Codex CLI、Antigravity CLI 默认禁用；Codex/Claude MCP 配置使用临时隔离目录，Antigravity 使用带锁和恢复语义的受控例外。

## 自动验收结果

执行：

```bash
./scripts/release-check.sh
```

2026-08-05 结果：

- Backend：`422 passed`
- Frontend：`23` 个测试文件、`171 passed`
- Production build：TypeScript 检查和 Vite build 通过
- Onboarding smoke：health → 幂等首次对话 → 张楚岚演示回复 → completed
- Conversation smoke：连续任务、定向王也、会话隔离等 `6/6` 检查通过
- Mission smoke：complex 四轮、多 Provider、artifact 和 tool events 检查通过
- `git diff --check`：通过

## 真实 CLI 发布门禁

以下命令不会包含在默认验收中，因为会使用已登录 CLI 并可能产生费用：

```bash
./scripts/release-check.sh --real-cli
```

通过标准：

- Codex 至少完成一次 MCP `publish_message` 真实对话。
- Claude Code 和 Antigravity CLI 若在本次发布环境中声明可用，应分别完成一次定向验证；不可用时必须显示明确的安装、认证或超时诊断。
- Provider 标签必须与实际执行 provider 一致，不得出现 Claude 标签下声称由 Codex 发言等上下文错配。
- 验证失败不得覆盖已保存角色路由，演示模式仍可继续使用。

## 当前产品边界

- 产品为 local-first MVP，不提供托管服务、远程账号系统或多用户权限模型。
- 用户插话会在 turn 间消费，不会强制杀死已经开始的 CLI turn。
- Antigravity CLI 没有稳定的 stream-json 输出，完成判定仍以 MCP `publish_message` 为准。
- partial text 仅保存在进程内存中；刷新可能丢失进行中的 partial，但不会丢失最终落库消息。
- 项目代码采用 MIT License；发布渠道仍由项目所有者确认。

final result: passed（offline release candidate）
