# AgentClub

> 把 Codex CLI、Claude Code 和 Antigravity CLI 组织成一支可配置、可协作、可复盘的本地 Agent 团队。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-2f6f64.svg)](CHANGELOG.md)

**简体中文** | [English](README_EN.md)

## AgentClub 是什么？

当用户同时拥有多个 AI CLI 时，真正困难的往往不再是“如何调用模型”，而是如何分配任务、保持上下文、处理失败，并看清每个 Agent 到底完成了什么。

AgentClub 是运行在本地 AI CLI 之上的 Agent 团队工作台。它把一次用户目标转换成一段可观察的协作流程：三位性格不同的伙伴交流观点、回应彼此，并按用户需求推进工作；底层再由 Provider Router 选择合适的 CLI 完成每一轮工作。

```text
用户目标
   ↓
Agent 团队协作：不同性格 / 共享能力 / 连续对话
   ↓
Provider Router：主 Provider + fallback
   ↓
Codex CLI / Claude Code / Antigravity CLI / 演示模式
```

因此，AgentClub 不要求用户围绕某一个模型重新建立工作方式。角色、任务流程和会话上下文保持稳定，模型则作为可以配置和替换的执行能力接入。

## AgentClub 能够做什么？

### 让用户先跑起来

项目内置不调用真实模型的演示模式。即使没有安装 CLI、没有登录模型账号，用户也能先完成第一次角色对话，确认任务创建、Agent 回复和页面状态链路都能正常工作。

### 把一个目标交给多 Agent 协作

AgentClub 会根据任务复杂度安排角色轮次，也支持定向选择某个角色。张楚岚通透机敏、重视人情与现实；冯宝宝坦率好奇、关注具体事实；王也松弛笃定、重视自主与长远选择。三人都可以提方案、分析、执行和总结，立场随事实与交流改变。未点名时按配置顺序安排发言，不按工种选人；多角色点名仍按点名顺序讨论。

人格约定、提示词对比和体验验收见 [三位讨论伙伴的人格约定](docs/PERSONALITIES.md)。

### 统一管理多个 AI CLI

用户可以在同一个设置页检测安装、完成认证、验证真实对话，并为每个角色选择主 Provider 和 fallback。当前支持 Codex CLI、Claude Code、Antigravity CLI，以及始终可用的演示模式。

### 看清 Agent 的执行过程

页面会持续展示任务状态、Agent 轮次、实际 Provider、fallback 尝试、MCP 工具调用、A2A 路由、产物归属和阻塞原因。用户不仅能看到最终回答，也能判断任务是如何完成或为什么失败的。

### 保留连续上下文和本地产物

同一会话可以持续追加任务，历史会话可以归档和恢复；Agent 产物按 mission 隔离保存。会话、任务状态和产物默认留在本机，便于继续工作和复盘。

## AgentClub 的核心优势

### 1. Provider 与角色解耦

这是 AgentClub 最核心的产品设计。角色定义“谁在工作”，Provider 定义“由哪个模型执行”，路由配置则决定“优先使用谁、失败后切换给谁”。

| 层级 | 负责什么 | 示例 |
| --- | --- | --- |
| 角色层 | 人格、关注点、交流习惯、会话身份 | 通透机敏的张楚岚、坦率好奇的冯宝宝、松弛笃定的王也 |
| Provider 层 | 模型能力、CLI 命令、认证和运行方式 | Codex CLI、Claude Code、Antigravity CLI |
| 路由层 | 主 Provider、fallback 和失败切换 | Claude → Codex → 演示模式 |

这种解耦带来四个直接价值：

- 只验证一个 Codex，也可以先让完整团队运行起来；
- 可以根据任务特点，为不同角色分配不同 Provider；
- 某个 CLI 不可用时，可以自动尝试 fallback，而不是让整项任务中断；
- 更换模型不会改变角色身份、协作逻辑和已有会话心智。

### 2. 先体验价值，再完成配置

AgentClub 把“产品是否可用”和“真实模型是否配置完成”拆成两个阶段。用户先通过演示模式获得第一次成功，再按 Codex-first 路径接入真实 CLI，之后按需扩展 Claude Code 和 Antigravity CLI。验证失败不会覆盖当前团队配置，也不会让聊天室失去可用状态。

### 3. MCP 是可审计的 Agent 通信链路

真实 Provider 启用 MCP 后，只有 `publish_message` 才会形成角色发言；CLI stdout 只作为调试信息。Agent 还可以通过 MCP 读取上下文、写入产物、更新状态和调用其他 Agent，使消息、工具和产物都能落到同一条可追踪的任务记录中。

### 4. Local-first，同时保留可观察性

AgentClub 将会话、设置和产物保存在本地，但没有因此牺牲运行透明度。SSE 实时状态、轮次追踪、工具事件、阻塞诊断和归档恢复共同构成了一套本地可调试的 Agent 工作环境。

### 5. 从单次回答升级为团队工作过程

产品关注的不只是生成一段文本，而是围绕目标形成“拆解—执行—检查—收口”的过程。不同人格、连续会话、Agent 接力和产物归属，为后续扩展团队模板、任务工作流和模型路由策略提供了基础。

## 项目演示

### 零账号启动与 Codex-first 引导

先用演示模式完成第一轮角色对话，再由页面引导用户验证 Codex CLI；验证失败不会影响已经可用的演示模式。

![AgentClub 新用户引导](docs/images/agentclub-onboarding.jpg)

### CLI 接入与团队配置

在设置页集中查看 CLI 安装、认证和验证状态，并为三位角色配置主 Provider 与 fallback。

![AgentClub CLI 接入设置](docs/images/agentclub-cli-settings.jpg)

![AgentClub 团队配置](docs/images/agentclub-team-settings.jpg)

## 快速开始

### 环境要求

- macOS 或 Linux shell 环境
- Python 3.11+
- Node.js `20.19+` 或 `22.12+`
- npm

首次体验不要求安装任何真实 AI CLI。

### 一键启动

克隆项目并在仓库根目录运行：

```bash
git clone https://github.com/Buaixihongshi/AgentClub.git
cd AgentClub
./scripts/start.sh
```

脚本会自动：

1. 创建 Python 虚拟环境并安装后端依赖；
2. 安装前端依赖；
3. 启动 FastAPI 和 Vite；
4. 等待健康检查通过；
5. 打开 `http://127.0.0.1:5173/`。

进入页面后，按照引导先与张楚岚完成一轮演示对话即可。按 `Ctrl-C` 会同时停止前后端服务。

常用启动选项：

```bash
./scripts/start.sh --no-open          # 不自动打开浏览器
./scripts/start.sh --no-install       # 跳过依赖安装检查
./scripts/start.sh --reset-onboarding # 重置首次使用引导
```

端口冲突时可以指定其他端口：

```bash
PORT=8001 FRONTEND_PORT=5174 ./scripts/start.sh --no-open
```

## 第一次使用

推荐按以下顺序完成配置：

1. **先跑演示对话**：确认页面、任务创建和角色回复链路正常。
2. **接入 Codex CLI**：在引导页按提示安装或登录 Codex，然后运行一次真实对话验证。
3. **启用完整团队**：验证成功后，可让三位角色暂时共用 Codex，立即体验多 Agent 协作。
4. **按需扩展 Provider**：在“设置 → CLI 接入”中继续验证 Claude Code 或 Antigravity CLI。
5. **调整团队路由**：在“设置 → 团队配置”中为每个角色选择主 Provider 和 fallback。

真实 CLI 验证会调用对应账号完成一次模型请求，可能计入套餐额度或产生费用。安装、登录、版本检测和演示模式本身不会调用真实模型。

### Provider 说明

| Provider | 命令 | 用途 | 仓库默认状态 |
| --- | --- | --- | --- |
| 演示模式 | 内置 | 无账号完成首次体验 | 启用 |
| Codex CLI | `codex` | 推荐的首个真实 CLI，可供全队共用 | 禁用，验证后启用 |
| Claude Code | `claude` | 可选的角色 Provider | 禁用，验证后启用 |
| Antigravity CLI | `agy` | 可选的角色 Provider | 禁用，验证后启用 |

AgentClub 不会在启动时启用真实 Provider。安装方式和登录命令会在引导页及设置页中显示；验证失败也不会覆盖已保存的角色路由，演示模式仍可继续使用。

## 工作方式

```text
React / Vite 页面
       │ REST + SSE
       ▼
FastAPI ── MissionRunner ── Provider Router ── 本地 AI CLI
   │              │                              │
   │              └──────── MCP tools ──────────┘
   ▼
SQLite 事件与会话记录  +  artifacts/<mission_id>/
```

- FastAPI 创建 mission，并由后台 runner 按工作流执行各角色轮次。
- Provider Router 根据团队配置选择真实 CLI 或演示模式，并在失败时尝试 fallback。
- MCP 是 Agent 与 AgentClub 之间的通信协议，负责发言、上下文、产物和 Agent 间调用。
- 前端通过 SSE 获取最新任务快照，并呈现完整轮次和诊断信息。

## 本地数据与隐私

AgentClub 是 local-first 项目，不提供远程账号系统或托管数据服务。默认运行数据保存在：

| 路径 | 内容 |
| --- | --- |
| `data/` | SQLite 会话、任务和本地设置 |
| `artifacts/` | Agent 生成的任务产物 |
| `.yiren/` | 本地启动日志和依赖状态标记 |

这些目录均不会进入发布仓。Codex 和 Claude 的 MCP 配置使用临时隔离目录，不会把 AgentClub 的 MCP 配置永久写入用户的全局配置；Antigravity 使用带锁、恢复和异常恢复语义的受控生命周期。

在公开仓库或提交 issue 前，请不要上传上述目录。项目提供发布隐私检查：

```bash
python3 scripts/privacy-check.py
```

## 手动开发

### 后端

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
PYTHONPATH=. ./.venv/bin/python -m uvicorn backend.app.main:app \
  --host 127.0.0.1 --port 8000
```

后端 API 默认位于 `http://127.0.0.1:8000/api`，健康检查为 `/api/health`。

### 前端

另开终端：

```bash
cd frontend
npm ci
npm run dev
```

Vite 默认运行在 `http://127.0.0.1:5173/`，并将 `/api` 代理到本地后端。

## 测试与发布验收

运行离线发布门禁：

```bash
./scripts/release-check.sh
```

它会执行完整后端测试、前端测试、生产构建，以及首次引导、连续会话和多 Agent 任务冒烟测试，不会调用付费模型。

真实 CLI 验收需要本机已经完成对应 CLI 登录，并可能产生模型费用：

```bash
./scripts/release-check.sh --real-cli
```

其他常用命令：

```bash
./.venv/bin/python -m pytest backend/tests -q
cd frontend && npm test
cd frontend && npm run build
```

## 项目结构

```text
AgentClub/
├── backend/              # FastAPI、任务编排、Provider adapter、MCP、SQLite
├── config/               # Provider 与三位角色的提交配置
├── frontend/             # React、TypeScript、Vite 页面
├── scripts/              # 一键启动、冒烟测试、发布和隐私检查
├── docs/                 # 验收说明和架构约束
├── artifacts/.gitkeep    # 本地产物目录占位
├── requirements.txt
└── README.md
```

## 常见问题

**没有 Codex、Claude 或 Antigravity 账号，能使用吗？**

可以。演示模式默认启用，足以跑通创建任务、角色对话、多 Agent 调度和产物链路。

**为什么安装了 CLI，页面仍显示“待验证”？**

“已安装”只代表命令可执行；还需要完成 CLI 登录，并在引导页或设置页运行一次真实 MCP 对话验证。

**验证真实 CLI 失败会影响当前团队吗？**

不会。只有验证成功并确认保存后才会更新路由；失败时会保留原配置。

**如何查看启动失败原因？**

检查 `.yiren/backend.log` 和 `.yiren/frontend.log`。端口被占用时，启动脚本也会显示占用进程。

**归档会删除历史聊天吗？**

不会。归档只是将会话移出当前列表，可以从左下角的归档入口恢复。

## 当前边界

- 当前版本是本地单用户 MVP，不包含云端部署、多用户权限或远程同步。
- 用户插话会在轮次之间消费，不会强制终止已经开始的 CLI 进程。
- 真实 Provider 的可用性取决于对应 CLI 版本、认证状态和账号额度。
- Agent 产物应写入任务对应的 `artifacts/<mission_id>/`，不要把敏感工作区直接用于真实模型验证。

## 参与贡献

欢迎通过 issue 提交问题、复现步骤和改进建议。提交代码前请至少运行：

```bash
./scripts/release-check.sh
python3 scripts/privacy-check.py
```

发布记录见 [CHANGELOG.md](CHANGELOG.md)，验收状态见 [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)。

## License

AgentClub 基于 [MIT License](LICENSE) 开源。
