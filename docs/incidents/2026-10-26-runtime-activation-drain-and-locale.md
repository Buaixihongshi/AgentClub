# 2026-10-26 日常环境版本切换阻塞与进程探针排查

日期：2026-10-26（Asia/Shanghai，UTC+8）。
环境：日常 runtime（`127.0.0.1:8000` / `localhost:5173`），原运行 SHA `24c0a628`（PR #31），目标切换 SHA `d7c11eac`（PR #32）。
数据库：`runtime-state/data/yirenhub.sqlite3`。

---

## 结论与根本原因

在用户尝试将日常环境切换至 PR #32 最新版本 `d7c11eac` 时发生连环阻塞，直接导致切换命令失败、浏览器界面显示会话空白或版本核对错误。事故共有三层独立但相互叠加的根因：

1. **业务层：活跃任务未终态阻塞 `/api/runtime/drain`（安全写拦截生效）**
   - 冯宝宝在会话 `c_e324d09997b0` 执行任务 `m_06c4ce6c78da46e6ab88d0b85748e9fa` 时，在发布候选准备就绪并向会话输出“候选已就绪，请在外部终端执行 activate”后，未调用 MCP 工具 `update_status` 将任务显式设为 `completed`。
   - 底层 Antigravity 适配器在发布消息后仍挂起等待后台任务，未产生 `result` 终态事件。
   - 该任务在 SQLite 中持续保持 `status: "running"`，导致热切换安全检查 `/api/runtime/drain` 返回 HTTP 409 `{"message": "服务仍有未完成工作", "active_missions": 1}`。用户无法在线完成服务热注销。该阻滞直到 1800 秒空闲硬超时触发后才自动解除（任务置为 `blocked`）。

2. **工具层：中文系统默认 Locale 导致 Supervisor 探针误判（`Unmanaged runtime ports are occupied`）**
   - 负责进程所有权生命周期校验的 `scripts/environment_supervisor.py` 中，`process_start(pid)` 函数通过 `subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], ...)` 获取 Supervisor 的进程创建时间。
   - macOS 系统的当前终端环境变量为中文 Locale（`LANG=zh_CN.UTF-8`），导致 `ps` 输出的日期时间格式为中文（例如 `一 10月/26 14:58:23 2026`），而 `instance.json` 中保存的是英文格式（`Mon Oct 26 14:58:23 2026`）。
   - 字符串完全比对 `process_start(pid) == supervisor_started_at` 恒返回 `False`，使得运行中的管理进程被误判为“非受管孤儿进程”，切换脚本拒绝清理并报错：
     `ERROR: Unmanaged runtime ports are occupied; existing services were not stopped`。

3. **系统层：停机后 TCP 端口释放微延迟竞态（Race Condition）**
   - 在使用显式 `LC_ALL=C` 绕过 Locale 校验执行 `activate` 时，旧实例 Supervisor 收到 SIGTERM 并杀死子进程，将自身状态置为 `stopped` 并返回。
   - 但子进程（Uvicorn / Vite）在操作系统内核层面的 TCP Socket 关闭及 `TIME_WAIT` 清理有数百毫秒的时延，而切换脚本未做任何等待便立即尝试拉起新候选实例并绑定端口，触发 `choose_ports` 抛出 `Ports are occupied: (8000, 5173)`。
   - 切换状态因拉起失败被迫转入 `recovery_required`。

---

## 对应任务与数据证据

- **阻塞任务**：`m_06c4ce6c78da46e6ab88d0b85748e9fa`，所属会话 `c_e324d09997b0`。
- **关键事件链**：
  - `12604`：`publish_message` 发布外部执行指令。
  - `12608`~`12613`：用户追问并中断（`@冯宝宝 我应该到哪个目录下执行该命令呢？`、`@冯宝宝 人呢`、`@王也 现在是怎么回事`），但由于前次回合仍在占用 worker，中断请求被排队未获应答。
  - `12615`：`2026-10-26T09:41:25Z` 触发 `turn.blocked`（`timeout after 1800s root agent idle`），任务状态转为 `blocked`，`active_missions` 归零。
- **备份快照**：
  - 事故恢复前快照备份：`.backups/0da2b92e3656471fbea08400e04ebaa8`
  - 最终成功切换快照：`.backups/d0bd731641944946955e3b66ead7e299`

---

## 处置与修复

1. **应急处置（已完成）**：
   - 确认活跃任务进入 `blocked` 终态后，执行 `recover` 解除 `recovery_required` 过渡状态；
   - 显式在 `LC_ALL=C` 环境变量下执行 `activate` 命令，成功完成原子切换并在浏览器中验证服务健康。
   - 当前版本前后端一致为 `d7c11ea`，各会话查看与切换正常。

2. **代码级工程加固（已在分支 `codex/fix-locale-and-ports` 实现并测试）**：
   - `scripts/environment_supervisor.py`：在 `process_start(pid)` 中强制向 `subprocess.run` 传递 `LC_ALL=C`，彻底免疫操作系统 Locale 差异；
   - `scripts/environment_supervisor.py`：细化子进程异常退出时的错误提示，打印具体子服务索引、PID 及退出码；
   - `scripts/releases.py`：在 `switch()` 与 `recover()` 中调用 `manager.stop()` 后，增加最长 5 秒的端口回收轮询等待（`ports_free()`），消除旧进程关闭微延迟引起的端口冲突。
   - `backend/tests/test_environment_lifecycle.py` 与 `backend/tests/test_release_switch.py`：新增对应单元测试并通过。
