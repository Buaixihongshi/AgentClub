# CLI 故障诊断与跨任务产物交接契约

来源：[2026-10-16 事故记录](../incidents/2026-10-16-cli-timeout-and-handoff.md)。

## 失败诊断

- 登录预检失败不代表最终认证失败。聚合日志必须尊重成功/失败发生顺序；成功后的新失败仍应提示。
- CLI 内部超时和宿主硬超时分开记录。兼容 supported CLI 在 stdout 或 stderr 输出的已知格式，不只匹配旧版本单一字符串。
- 无 MCP `publish_message` 时不把 stdout 当作发言；进程被终止不代表之前的文件修改被撤销。
- 默认诊断保存本机受限目录，限制每条大小及保留条数，脱敏常见凭据，不默认复制 MCP config/env，不返回绝对日志路径或内容到聊天。
- 诊断 IO 失败不能覆盖原本的调用结果。测试覆盖正常退出、宿主超时、provider 内部超时及写入失败。

## 超时配置

- `hub_settings.json` 用户设置优先于环境和默认值。修改默认值不能迁移用户的显式设置。
- 变更生效值后验证设置 API、`snapshot_settings` 和创建的 provider adapter；每个新任务读取新快照。
- 无 reload 的服务需要重启才能加载 Python 实现变更。先查活动任务，保留数据库和配置备份，再重启、验证健康与历史会话。

## 产物交接

- 产物标识为 `(source_mission_id, relative_path)`，相同文件名不能跨任务去重。
- prompt 产物索引包含同一会话的历史任务，明确提供来源 ID；读取通过 MCP `read_artifact` 传递该 ID。
- 未指定来源时只读当前任务。显式跨任务时，两任务必须属于同一非空 conversation；不允许无 conversation 的任务互读。
- 在来源任务的产物目录中 resolve 路径并校验包含关系；拒绝 `..` 和符号链接越界。
- 回归至少覆盖历史产物发现、stdio MCP 来源参数传递、同名不同来源、跨会话拒绝、空会话拒绝、路径及符号链接越界。
