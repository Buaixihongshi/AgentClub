# GPT-6.1 Sol 可用性与评测 MCP 审批排查

日期：2026-10-19。

结论：GPT-6.1 Sol 当前可通过本机 Codex CLI 正常推理。首次评测确实收到了提供方的容量错误，但后续排查无法稳定复现；不能据此把模型持续不可用作为暂停的完整解释。进一步发现并修复了评测端的 MCP 审批阻塞，以及失败回合漏记用量的问题。

## 对照诊断

固定 `gpt-6.1-sol`、low、相同短问题、相同认证来源、空白临时工作目录与原生工具限制；不切换模型。两款 CLI 的全套评测没有在本次诊断中重启。

| 条件 | 模型推理 | MCP 发布 | 回合 |
| --- | --- | --- | --- |
| 原评测隔离配置，Codex 默认 Provider | 成功，产生 turn.completed 与用量 | 被审批策略拒绝 | blocked |
| 隔离配置加本机 custom Provider / WebSocket 连接参数 | 成功，产生 turn.completed 与用量 | 同样被拒绝 | blocked |
| 原评测隔离配置，加本地 MCP 定向审批配置 | 成功 | 成功发布 `OK` | completed |

本机配置与评测配置存在 Provider 差异，但两种连接方式均产生了模型回复。本次证据不支持把 Provider 差异直接认定为容量错误的根因，也没有因此复制用户整份全局配置或改变原模型路由。

两个失败诊断的原始 MCP 错误均为：`MCP tool call requires approval, but approval policy is never`。

原评测组合启用了只读沙箱及禁止交互审批，而本地 MCP 工具没有单独审批配置。模型能完成推理，却无法通过 `log_thought` / `publish_message` 形成正式发言；单看 Mission blocked 状态会把这两种失败混淆。

## 修复

`backend/app/evaluation/real_pilot.py` 的 `IsolatedCLI._render_codex_config` 为本地评测协议的四个工具设置独立 `approval_mode=approve`。保留只读沙箱与原生命令禁用，不对其他服务器、文件读写或命令做全局放行。评测 MCP 服务继续只向 D 暴露 invoke_agent。

该设置依据 [OpenAI 官方 MCP 配置说明](https://learn.chatgpt.com/docs/extend/mcp)中的逐工具审批配置；真实调用验证确认已生效。

`backend/app/adapters/mcp_subprocess.py` 将用量持久化移至退出处理的 finally，保证已取得用量的失败回合也记录一次，避免仅成功回合计费；缺少提供方用量的原容量失败仍记未知。

新增测试覆盖定向审批范围、发布失败后的用量、非零退出后的用量。相关测试共 61 项通过。

## 真实验证与成本

| 条件 | 输入 token | 缓存输入 token | 输出 token | 未应用缓存折扣的 API 等价费用 |
| --- | ---: | ---: | ---: | ---: |
| 默认隔离 | 32,090 | 20,864 | 146 | $0.065640 |
| 本机连接参数 | 32,011 | 29,312 | 135 | $0.065372 |
| 定向审批修复 | 32,019 | 29,312 | 103 | $0.065068 |

合计 $0.196080；实际 CLI 套餐扣费未知。费用采用已冻结的标准 API 基础费率估算，不计缓存折扣。前两次诊断的用量从原始 CLI `turn.completed` 恢复，单独保存，不改写原始失败记录。修复后的第三次用量已由 adapter 自动持久化。

原首轮评测的已知开销 $0.0904065 另行保留；加上本次诊断，已知 API 等价费用累计 $0.2864865，原容量失败仍有一次费用未知。后续重跑应带入这些历史成本，不通过换目录重置费用。

证据目录：`output/eval/codex-availability-20261019/`，包含三次诊断结果、原始数据库/CLI 诊断、恢复的用量与最小复现脚本。此处是连接诊断，不作为 A/B/C/D 能力评测样本。
