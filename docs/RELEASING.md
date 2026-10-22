# 审批、发布与恢复

本工具将“写好代码”“准许合并”“合入 main”“激活日常实例”分开记录。
Agent 可准备和验证候选；Owner release、实际数据导入、activate、rollback、recover 由所有者明确执行。
共用账号凭据无法技术上区分人和 Agent，这一边界需要独立凭据，不能由本地批准字段伪造。

## 1. 当前 head 的审批门禁

```sh
./scripts/release.sh prepare --pr 22
./scripts/release.sh gate status
./scripts/release.sh gate install
```

prepare/status 只读。gate install 是本流程改造授权范围内的仓库设置变更：先读取 main 上
真实存在的人工 workflow，再在现有 no-bypass 主线规则中增加 Actions 来源的 Owner approval，
保留前后端 required checks、strict 更新、PR、删除与 force-push 保护；不触发批准或合并。
工具会回读规则确认生效。旧 v1 workflow 足以启动此门禁；PR 3 合并后才显示 hardened=true。

所有者在 GitHub Actions 的 Owner release 选择 main，输入 PR 编号和 prepare 列出的完整
head SHA。新 workflow 只在 owner/main 执行，checkout 固定到触发时的 main SHA，并拒绝
draft、关闭、非 main 基线和已过期 head。追加提交不会继承旧批准。Agent 不运行此 workflow。

连续 PR 在父分支上只完成候选检查，prepare 会标记尚未具备 main 放行条件。依次合并
PR #22、将 #23 改基到 main 后验证/放行，再对 PR 3 做同样操作。

## 2. 准备准确的已合并候选

```sh
./scripts/release.sh stage --sha <完整的已合并提交SHA>
./scripts/release.sh status
```

stage 验证提交在当前公共 main 历史中，找到它对应的实际合并 PR，检查该 PR head 的两项
Actions CI 和 Owner approval。批准必须来自本仓库 main 上由所有者手动触发的 Owner release。
生成可追溯链：source_pr → approved_head_sha / approval_run → merge_commit_sha → deployed sha。
旧的、没有该审批链的历史提交不能冒充已批准版本。

候选在环境根目录 releases/<sha> 的独立 detached worktree 安装、完整检查和生产构建。
前端按 runtime 标签构建并保留构建时身份；manifest 记录源码摘要、完整检查报告和所有
dist 文件校验值。报告或产物变动、检查缺项、dirty checkout 均阻止激活；复用候选先复核
这些事实，不重编译正在运行的版本。stage 不启动或停止日常服务。

所有本机路径、检查报告、connection-secrets、备份、current.json、transition.json 和
release-history 都留在私有环境根目录，不提交到仓库。

## 3. 首次状态准备与日常切换

已有服务不得用空状态替换。首次迁移按以下顺序由所有者操作：

1. 在旧界面处理完运行/等待任务，确认 CLI 与维护工作结束，停止原启动终端。
2. import-state 检查旧 checkout 不再监听、数据库没有未完成工作，再以 SQLite backup
   获取一致快照，复制设置、连接密钥、角色配置和产物；保留原目录与原始数据。
3. 核对导入内容及 stage 候选，再明确激活。原服务仍占用端口时工具拒绝，不按端口杀进程。

```sh
./scripts/release.sh import-state --from-checkout /absolute/path/to/old-checkout
./scripts/release.sh activate --sha <候选SHA> --expected-current none
```

默认导入旧 checkout 的 data/yirenhub.sqlite3、config、artifacts；自定义布局显式使用
--database、--config、--artifacts。来源必须是 AgentClub 数据库。复制不跟随产物中的目录
符号链接，不回写原库。导入以准备目录原子落位，不覆盖已有 runtime-state。
只有确实不需要任何旧数据的新实例才使用 `release.sh init --empty`。

后续切换必须给出实际当前 SHA，防止覆盖别人刚完成的发布：

```sh
./scripts/release.sh activate --sha <下一候选SHA> --expected-current <当前部署SHA>
```

工具重新核对来源与 required owner gate，认证并 drain 当前受管实例，等待已接入写操作、
确认没有活跃工作；一致备份 SQLite、设置、密钥和产物后停止自己的 supervisor，启动新候选。
生产前端使用已验证的 preview 产物，保留同一 runtime-state；不会覆盖已保存的 Provider 配置。
新实例健康与前后端身份核对成功后，才原子更新 current.json 和历史记录。

新版本启动失败且能证明新进程已退出时，恢复快照并重启已验证前版；若新进程仍活着或
身份不明，记录 recovery_required，绝不在活跃写入者下恢复数据库。

## 4. 回滚与中断恢复

```sh
./scripts/release.sh rollback --expected-current <当前部署SHA> --restore-state
./scripts/release.sh recover --expected-current <current.json记录的SHA或none>
```

**rollback 同时恢复前一版本和发布前状态。** 它先保存当前状态的另一份一致备份，因此
发布后新增的聊天/产物不会被直接删除，但不留在回滚后的活动库中；可从保留的备份恢复。
必须显式 --restore-state，不自动假设旧代码兼容新数据。回滚使用已有、校验通过的候选
及批准链，不要求网络可用或制造一次新批准。

transition.json 在暂停接入前即记录意图，随后记录快照、旧版停止、新版启动和提交阶段。
存在未完成转换时阻止新切换。recover 可重复检查当前实例：若目标已经正式提交且身份
一致则补齐记录；否则在确认归属、空闲和监听者已退出后保留当前状态、恢复快照与前版。
未知进程、busy 工作或校验失败会要求先处理具体问题，不能通过重新启动掩盖状态。

current/transition/history 的记录是操作证据，不是所有者授权。临时 fixture 测试不写真实
GitHub 批准，不使用日常端口或生产数据。备份与历史保留不自动清理；清理需要单独核对。
