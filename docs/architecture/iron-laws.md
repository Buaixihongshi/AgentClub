# Unity Iron Laws（L1–L4）

Unity 在 ADR、`CLAUDE.md` 与本仓库 MCP 语义下实际执行的四条**铁律**：破坏任一条应由 `backend/tests/test_iron_laws.py` 在 CI 中拦截。

---

## L1: stdout 不会升格为 agent 消息（`mcp_enabled=true` 时）

**Rule：** 对 MCP 已启用的 provider，进程 stdout **绝不**被合成为 `event_type=message` 里的 agent 发言；若本回合未通过 MCP 调用 `publish_message`，该回合判定为 **BLOCKED**。

**Why：** 防止模型只在终端打印「我已经回复了」却未真正写入消息流；保证 `messages` / 事件流与真实 MCP 工具调用一致，可审计。

**Code anchor：** `backend/app/adapters/mcp_subprocess.py`（约 216–223 行：在未检测到 MCP `publish_message` 时返回 `BLOCKED` 与固定 summary）。

**Test：** `test_l1_stdout_does_not_become_agent_message`

**违反示例：** CLI exit 0、stdout 写有用户可见回复文本，但未调用 MCP `publish_message`。

---

## L2: MCP 配置必须 Dynamic；禁止写入用户全局 CLI 目录

**Rule：** `~/.codex/`、`~/.claude.json`、`~/.gemini/`（及同类用户级路径）不得在业务代码中作为**写入目标**；真 CLI 侧配置必须使用 `tempfile.TemporaryDirectory()` 等临时目录并结合环境变量（如 `CODEX_HOME`、`HOME`）挂载。仅从用户目录**读取**凭证并 `copy` 到临时目录时，须落在 `_seed_*` 一类函数内，且语义为「读源、写临时盘」而非回写全局配置。

**Why：** 多实例并发互不污染、卸载无痕、不把试验性 MCP 路由写进用户本机 CLI 的全局状态。

**Code anchor：** `backend/app/adapters/mcp_subprocess.py` 中 `_seed_codex_auth`、`_seed_gemini_auth`（及与临时 `CODEX_HOME` / 临时 `HOME` 相关的配置写入路径）。

**Test：** `test_l2_no_writes_to_user_cli_home`

**违反示例：** 向 `Path("~/.codex/...").expanduser()` 或等价路径执行 `open(..., "w")`、`write_text`、`shutil.copy2(..., ~/.codex)` 等以用户家目录为 **dst** 的写入。

---

## L3: A2A 仅经 `invoke_agent`，禁止从消息正文做 @mention 解析路由

**Rule：** Agent 间协作路由必须经由 MCP 工具 `invoke_agent`（及 runner 侧的 `a2a_enqueue` 等配套机制）；`@张楚岚` 等仅作展示文案，不得通过正文 `split('@')`、`startswith('@')`、针对 `@` 的正则等方式推断 `agent_id`。

**Why：** 文本路由不可审计、易被模型误解析，且会绕过 `max_a2a_invocations` 等预算与事件记录。

**Code anchor：** `backend/app/mcp/tools.py` 中 `invoke_agent`（入队 / 落库 `a2a.request` 或与 `a2a_enqueue` 交互的逻辑）。

**Test：** `test_l3_a2a_only_via_invoke_agent`

**例外（显式 allowlist）：** `backend/app/orchestration/speaker.py` 对 API 字段 `target_agent_id` 的处理属于**显式请求参数**，不是从消息体解析 `@xxx`；测试中对 `speaker.py` 的静态规则与对 `invoke_agent` / `a2a_enqueue` 的约束分别说明。

**违反示例：** 在 `orchestration/` 或 `api/` 中根据用户消息字符串解析 `@` 来改写 speaker 或 workflow。

---

## L4: 默认 Fake CLI；仓库内真实 provider 保持关闭

**Rule：** `Settings.use_fake_cli` 默认为 `True`；提交到仓库的 `config/cli-providers.json` 中内部 `fake` 为 `enabled: true`，`claude` / `codex` / `antigravity` 为 `enabled: false`；已退役的 `gemini` 不得出现在 Provider 配置中。

**Why：** Clone 即可离线跑通；避免默认误连真实账号与配额。

**Code anchor：** `backend/app/settings.py`（`use_fake_cli` 默认值）、`config/cli-providers.json`（各 provider 的 `enabled` 字段）。

**Test：** `test_l4_fake_default_real_providers_disabled`

**违反示例：** 将默认 `use_fake_cli` 改为 `False`，把已提交的真实 Provider 改为 `enabled: true`，或重新加入 `gemini` Provider 配置。
