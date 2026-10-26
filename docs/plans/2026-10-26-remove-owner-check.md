# 取消单独的 Owner approval 门禁

所有者明确要求取消 PR 合并前的额外手动审核步骤。本次移除 Owner release workflow 及其状态检查，保留当前 head 的两项 Actions CI、PR、适用非作者审阅、strict 更新、禁止 force push/delete 与无 bypass 的分支保护。

## 实施与验证

1. 发布策略停止读取 Owner approval，改为记录实际合并 PR、CI 验证 head 与主线部署 SHA。
2. gate remove-owner 仅删旧状态项并回读，状态查询无副作用；异常保护不覆盖。
3. 删除旧 workflow/辅助程序，同步活跃流程文档、协作入口和 PR 模板。
4. 临时 fixture 回归精确 head CI、失败/未合并来源拒绝、规则保留、幂等与回读失败；新旧 manifest 的回滚都保留原始证据。
5. 提交准确版本并完整检查后，执行已授权远端规则调整；PR 记录实际配置变化与待审阅状态。历史批准、私有 manifest、日常数据和运行目录不改写。

移除这一检查不代表自动合并、自动部署或取消任务内的上线决定。alpha 继续只运行干净 main；runtime 切换保留独立授权、空闲核对、备份与恢复流程。
