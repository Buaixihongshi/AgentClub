# Alpha CLI 配置一致性排查

日期：2026-10-24（Asia/Shanghai）。

本文保留环境问题当时的排查与验证记录。修复现已纳入对话式自主任务升级的 PR 候选；下文“尚未提交”等描述对应原排查阶段，当前交付与剩余步骤见 [升级复盘](2026-10-24-conversation-first-autonomy-upgrade.md)。

## 原因

1. 自主任务功能仍在开发候选，尚未合入主线。5176 的 alpha 是干净主线 `9305049`，5180 才是包含自主功能的开发候选。此前的候选自验不能表述为 alpha 新功能验收。
2. 旧环境启动器明确禁止 alpha 使用真实 CLI，并强制演示模式。只在页面切换 provider 不能绕过该限制。
3. 此前 5180 三个角色全用 Codex，是验证时手动简化了路由，不是日常有效配置。这没有覆盖用户实际使用的混合 CLI 场景。

## 本次修正

独立工作目录：环境仓库下的 `features/alpha-cli-parity`，使用专用修复分支，基于 `9305049`。改动尚未提交、合入主线。

- alpha 启动时继承日常有效 CLI 配置，包括角色路由、模型、备用连接和超时；缺失配置或遇到不能继承的 API 路由时明确失败。
- 只复制允许的 CLI 配置字段，保留 alpha 独立的聊天、任务、数据库和产物，不复制日常数据库或 API 密钥文件。
- alpha 保持原有 `.yiren/alpha` 状态目录，真实 CLI 不另建空数据库。
- 保留 alpha 干净主线、源码版本和实例隔离检查。日常实例没有重启。
- 浏览器操作优先使用 ego-browser，已写入工作区约定。

alpha 已使用修正版启动器重启，源码仍是干净 `9305049`。当前 alpha 的真实 CLI 已启用。修正版尚未合入主线，旧版启动入口仍有演示限制；后续不能误用旧入口并认为修复已全局生效。

修正版启动入口（从上述独立目录执行）：

```sh
./scripts/env.sh start alpha --checkout ../../alpha --no-install --detach
```

配置继承发生在启动时，不是实时监听。日常设置变更后需显式同步或重新启动 alpha。

## 当前模型配置

用户追加要求把 Antigravity 改为 Gemini 3.8 Flash。本机 `agy models` 返回 High、Medium、Low 三档，选择 Medium，准确 ID 为 `gemini-3.8-flash-medium`。

已通过各环境设置 API 将三个角色的 Antigravity 模型覆盖配置同步到日常 8000、alpha 8300、开发候选 8400，并逐项核对除该模型字段外设置未变。实际主路由仍为：张楚岚 Codex、宝宝 Codex、王也 Antigravity，备用连接均为空。该设置作用于 AgentHub 发起的新调用，不改写独立运行 agy 时的全局默认模型。

## 验证与证据

- 环境与配置相关检查：42 passed。
- 修复分支后端全量：956 passed、1 skipped；diff 空白检查与隐私检查通过。
- alpha 重启前后原有会话、任务、消息和事件逐行一致；之后新增只读验收会话。
- 首次在真实 5176 页面 @王也，确实路由 Antigravity，但 CLI 返回余额不足 `RESOURCE_EXHAUSTED (429)`，任务阻塞。没有自动换成 Codex。
- 更换 Gemini 后通过 ego-browser 在真实 5176 页面新建只读验证任务：`m_7ad46ca79b584b2cbc5405931eeb712a`，会话 `c_e6a2c77b33ee`。王也使用 Antigravity，通过 MCP 成功回复 `gemini-flash-alpha-ok，我是王也。`；任务 completed，单次调用约 25.5 秒，无失败、无备用切换。本次只验证连通性，不代表完整多角色业务流程通过。

本机原始配置回读和任务证据保存在忽略目录 `output/alpha-cli-parity/`。alpha 原状态备份位于 环境仓库的 `.backups/alpha-cli-parity-20261024-011424`。

这些结果只说明环境修正和 CLI 连通性，不代表自主功能已合入主线、完整团队流程通过 alpha 验收或已批准上线。
