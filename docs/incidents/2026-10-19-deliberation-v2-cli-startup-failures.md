# V2 首次真实讨论：三个 CLI 在初始化阶段失败

日期：2026-10-19；修复验收：2026-10-20。状态：已修复，三种真实 CLI 的提交及完整六阶段连通性已通过；原失败讨论保持不变。

## 现场

- 任务：`m_6963dd9bc0854c33aaaca8450b1e986e`，议题“如何设计咱们的eval”。
- 首轮开始时间：北京时间 23:22:10；第二次尝试于 23:22:16 结束。
- 协议：`deliberation_v2`；规则：`deliberation-v2.2`；构建：`69419869aa5c6246`。
- 状态：`blocked`，阶段 `independent`，状态版本 24。
- 三个租约均为 `failed`、`attempt=2`、`context_read=false`。没有有效观点提交。
- 六次进程耗时 206～809 毫秒，没有达到模型执行超时。

## 分别发生的错误

| Agent / Provider | 现场错误 | 判断 |
| --- | --- | --- |
| 张楚岚 / Codex | `application network policy is unavailable` | 私有错误字段截断后保留的是次级网络警告。无凭据复现取得终止错误：`thread/start failed: failed to load configuration: Failed to synchronize managed preferences (code -32600)`。当前 macOS 沙箱与 CLI 系统偏好初始化不兼容；具体需要放行的系统访问尚待最小化定位。 |
| 冯宝宝 / Claude | `EPERM: operation not permitted, open '/tmp/claude-501'` | 适配器只允许本回合临时目录写入，真实 CLI 使用了另一个共享临时目录。未设置与沙箱一致的 CLI 临时目录。 |
| 王也 / Antigravity | `-p took "--new-project" as its prompt` | 配置中的参数末尾为 `-p`，适配器又在其后追加 `--new-project ...`，导致 `-p` 的下一个参数成为选项而非真正提示词。 |

相关实现：`backend/app/extensions/discussion/deliberation_cli.py` 的 `sandbox_profile()` 与 `run_turn()`。

## 复现与证据边界

1. 只读检查 SQLite 的最新 `discussion.v2.state`、`discussion.provider_execution` 和 `turn.failed` 事件。
2. 使用当前安装的 Codex、全新且无凭据的 `CODEX_HOME`、无效探针模型名进行初始化对照。沙箱内立即发生系统偏好同步错误；沙箱外通过此初始化步骤，随后因无凭据收到 401。探针未成功进行付费生成。
3. 清除继承的 `CODEX_*` 环境变量后，沙箱内错误仍然存在，不能将其简单归因于父应用环境变量。
4. 当前 `agy --help` 支持将提示词紧跟 `-p`；现场 CLI 自己明确报告参数位置错误。
5. 没有解封任何 Agent 正文，没有修改原任务状态，没有追加真实模型重试。

## 为什么界面难以诊断

`Deliberation.user_view()` 将所有 lease 错误统一替换成同一句“Provider 运行失败、超时或结构化提交无效”。保护封存正文是必要的，但它也遮蔽了可安全展示的启动错误类别与退出码。

`3/13` 统计的是已分配的阶段回合，不是进程尝试次数。本次为 3 个阶段回合、6 次进程尝试。每个回合最多尝试 2 次，因此显示“重试额度已用尽”；并非总阶段预算 13 次已经用完。

## 修复要求

1. Codex：最小化定位系统偏好初始化所需的系统访问，保留项目文件、数据库、其他回合文件和本地应用 HTTP 的隔离；不通过整体移除沙箱解决。
2. Claude：将 CLI 自有临时目录和子进程临时目录置于本回合允许目录内，验证实际初始化，不能放开整个共享 `/tmp`。
3. Antigravity：先组装所有选项，再以明确的 `-p=<prompt>` 形式绑定提示词；验证不同配置中的 print 参数，避免重复或误绑定。
4. 诊断：单独记录并展示不含封存正文的 Provider、失败类别、退出码；保留最终致命错误，避免先截断后只剩次级警告。
5. 验收：补充每种真实 CLI 初始化路径的检查。之前的状态机、假 CLI 和 `--version`/`--help` 检查没有覆盖这些路径，不能替代真实讨论验收。

## 2026-10-20 修复与验收

- Codex：为 `com.openai.codex` 放行只读偏好访问及 macOS `apple.cfprefs.` 共享缓存读取。真实 CLI 从“创建会话失败”变为可以读取上下文并提交。没有放开用户目录或偏好写入。其初始化调用可对照 [官方管理偏好加载实现](https://github.com/openai/codex/blob/main/codex-rs/config/src/loader/macos.rs)。
- Claude：将 `CLAUDE_CODE_TMPDIR`、系统临时目录和 XDG 缓存置于本回合目录。只从用户设置继承连接与模型环境字段，保留当前使用的本地模型代理及认证；不加载 hooks、项目配置或旧会话。仅白名单放行实际模型代理端口，其他本地端口继续拒绝。OAuth 场景可由宿主读取选中的凭据并传递访问令牌，子进程不读取用户 Keychain。
- Antigravity：参数统一绑定为 `-p=<prompt>`。本回合使用独立 HOME、MCP、缓存和会话目录，不再变更全局 MCP 文件。只复制安装状态与模型路由字段，通过只读的现有登录 Keychain 别名维持认证；允许本回合运行时绑定本地端口，但本地出站连接仍受限制。真实调用确认可以认证并提交。
- 诊断：保存失败类别和退出码，私有错误保留末尾，避免截掉最终致命错误。公开视图只使用预定义的脱敏提示；失败面板展示所有失败参与者，并分开显示阶段回合与实际尝试次数。

验证结果：

1. 后端全量：705 passed。后续针对最终改动的回归：36 passed、1 skipped（真实 CLI 初始化用例默认 opt-in）。
2. 单独开启真实 Codex 初始化检查：`YIREN_CHECK_INSTALLED_CLI=1 .venv/bin/python -m pytest backend/tests/test_deliberation_isolation.py -q`，8 passed。该初始化检查使用无凭据目录及不通的探针模型地址，不进行付费生成。
3. 前端：214 passed，生产构建通过。
4. 三个真实 Provider 分别完成 `read_context` → `submit_discussion_result`。
5. 使用实际生产 `MissionRunner`、提示词构造、ProviderRouter 和沙箱，在临时数据库 `/tmp/acv2-full-live-k86zcusg/check.sqlite3` 完成三人独立提交 → 显式选择冯宝宝综合 → 楚岚和王也独立复核 → 正常收束。共 6 个回合、6 次尝试，无重试。未向真实用户数据库添加验收任务。

本次实际模型调用用于连通性验收，议题是固定的“先验证”，不代表真实项目问题的讨论质量或减少附和效果已验收。原失败任务的重试额度不重置；服务更新后可停止该讨论并重新发起。
