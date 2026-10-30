# 每轮完整执行记录（P0）

## 问题与范围

Unity 是会话的权威记录，但此前没有保存 CLI 执行的完整过程：聊天路径不存发给模型的
提示词，工具参数/输出截断到 800/2000 字，原始流只在 cli-diagnostics 留最后 32KB 且全局
只留 100 条；讨论（deliberation_v2）与自主任务两条沙箱路径不写原生活动事件、会话 id，
失败时只留 stderr 末尾 1000 字。事后无法回答“模型看到了什么、做了什么、在什么环境下跑”。

本次只补宿主侧留存与读取接口。不改变沙箱 profile、CLI 参数（仍不让 CLI 自身落盘）、
提示装配、调度或门禁；不做前端展示（P1）和在原生工具中接手（P2）。

## 行为

- 每次 CLI 进程一份 JSONL：`<数据库目录>/turn-records/<mission_id>/<turn_id>.<record_id>.jsonl`。
  依次为 header（mission/会话/轮次/角色/provider/路径 chat|discussion|work）、environment
  （可执行文件、argv（提示词替换为 `<prompt>`）、cwd、Unity 改动过的环境变量名、隔离方式、
  沙箱 profile 原文及哈希、配置模型）、input（CLI 实际收到的提示词全文及哈希）、逐行
  stdout/stderr、session、exit（退出码、等待原因或终止原因、是否接受结构化提交、失败码、
  CLI 版本、行数、是否截断）。
- 私有与脱敏：目录 0700、文件 0600；每行写入前复用 cli-diagnostics 的脱敏规则；环境变量
  只记名称不记值（避免令牌落盘）。单条记录上限 16 MiB，超出后只计数不再写入，并在 exit
  标明 `truncated`；全部记录超过 1 GiB 时从最旧文件删起，保留当前记录。写入失败不影响轮次结果。
- CLI 版本通过 `<cli> --version` 获取，按可执行文件路径+mtime+大小缓存，3 秒超时；离线测试关闭。
- 沙箱路径改为边运行边逐行读取（替代一次性 communicate）。自主任务轮次实时写
  `provider.session` / `provider.activity`，与聊天路径共用 `NativeActivityRecorder`；
  讨论轮次只写私有记录，不写入 mission 事件，避免在批次公布前暴露未公开的提交。
- `GET /api/observability/turns/{turn_id}/records` 返回该轮全部记录；V2 讨论在 phase 为
  ended 前返回 409。

## 风险与恢复

- 记录含完整提示词（长期记忆、其他角色发言、工作区状态）与工具原始输出，只保存在本机
  数据目录，与 SQLite 同级、同权限，不上传。属于数据/安全变更，合并前需非作者审阅。
- 磁盘：单条与总量均有上限；删除 `turn-records/` 目录即可回到无记录状态，不影响数据库。
- 发布备份：`scripts/release_storage.py` 整份复制数据目录，记录会随数据库一起备份和回滚
  （与数据库保持一致），每份备份最多多出约 1 GiB。本次不改发布工具；是否在备份中排除
  记录由所有者另行决定。
- 未覆盖：`YIREN_ANTIGRAVITY_PERSISTENT` 的常驻 agy 进程（多轮共用一个流，默认关闭）、
  纯文本 API 连接（非 CLI）。模型思考内容仍按各 CLI 输出口径，不额外采集。
