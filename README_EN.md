# AgentClub

> Turn Codex CLI, Claude Code, and Antigravity CLI into a configurable, collaborative, and observable local Agent team.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-2f6f64.svg)](CHANGELOG.md)

[简体中文](README.md) | **English**

## What is AgentClub?

Once users have more than one AI CLI, calling a model is no longer the hardest part. The real challenge is deciding who should do the work, preserving context across turns, recovering from provider failures, and understanding what each Agent actually produced.

AgentClub is a local Agent team workspace built on top of existing AI CLIs. It turns a user goal into an observable collaboration process: three distinct personalities exchange views, respond to each other, and advance the work requested by the user. A Provider Router then selects the appropriate CLI for each turn.

```text
User goal
   ↓
Agent teamwork: distinct personalities / shared abilities / continuous dialogue
   ↓
Provider Router: primary provider + fallbacks
   ↓
Codex CLI / Claude Code / Antigravity CLI / Demo Mode
```

Roles, workflows, and conversation context remain stable while models are treated as configurable execution providers. Users can change the underlying CLI without rebuilding the way their Agent team works.

## What can AgentClub do?

### Get users to a first successful conversation

AgentClub includes a built-in Demo Mode that does not call a real model. Users can create a task and receive the first Agent response before installing a CLI, signing in to a model account, or spending model quota.

### Turn one goal into multi-Agent teamwork

AgentClub schedules roles according to task complexity and also supports directing a task to a specific role. Zhang Chulan is socially perceptive and pragmatic; Feng Baobao is candid, curious, and attentive to concrete facts; Wang Ye is relaxed, independent, and mindful of long-term choices. All three can propose, analyze, execute, and summarize. Their views can change with evidence. Without mentions, turns follow configuration order rather than job keywords; multiple mentions retain the selected order.

### Manage multiple AI CLIs in one place

The Settings page detects installed CLIs, guides authentication, verifies a real MCP conversation, and assigns a primary provider and fallbacks to each role. AgentClub currently supports Codex CLI, Claude Code, Antigravity CLI, and the always-available Demo Mode.

### Make Agent execution observable

The mission room shows task status, Agent turns, the provider actually used, fallback attempts, MCP tool calls, Agent-to-Agent routing, artifact ownership, and blocking diagnostics. Users can inspect both the result and the process that produced it.

### Preserve local context and artifacts

Users can continue work in the same conversation, archive and restore previous conversations, and keep generated artifacts isolated by mission. Conversation state, settings, and artifacts remain local by default.

## Why AgentClub?

### 1. Roles and providers are decoupled

This is the central product principle behind AgentClub. A role defines **who is working**. A provider defines **which model executes the work**. Routing determines **which provider to try first and what to use when it fails**.

| Layer | Responsibility | Example |
| --- | --- | --- |
| Role | Personality, perspective, communication habits, conversation identity | Perceptive Chulan, candid Baobao, independent Wang Ye |
| Provider | Model capability, CLI command, authentication, runtime behavior | Codex CLI, Claude Code, Antigravity CLI |
| Routing | Primary provider, fallbacks, and failure recovery | Claude → Codex → Demo Mode |

This separation creates four practical benefits:

- One verified Codex connection can power the complete Agent team.
- Different providers can be assigned to roles according to the task.
- A failed or unavailable CLI can fall back without stopping the entire mission.
- Changing the model does not change role identity, team workflow, or conversation context.

### 2. Value comes before configuration

AgentClub separates product activation from real-model setup. Users first complete a successful Demo Mode conversation, then follow a Codex-first onboarding path, and only add Claude Code or Antigravity CLI when needed. A failed verification does not overwrite the current team configuration or make the room unusable.

### 3. MCP provides an auditable Agent communication path

For MCP-enabled providers, only `publish_message` creates a visible Agent message; CLI stdout remains diagnostic output. Agents can also read context, write artifacts, update status, and invoke another Agent through MCP. Messages, tools, handoffs, and artifacts therefore belong to the same traceable mission record.

### 4. Local-first without becoming a black box

Conversation data, settings, and artifacts are stored locally, while SSE updates, turn tracking, tool events, blocking diagnostics, and archive recovery keep the runtime observable and debuggable.

### 5. From a single answer to a team workflow

AgentClub is designed around a process—plan, execute, review, and close—not just a generated response. Distinct personalities, continuous conversations, Agent handoffs, and artifact attribution provide a foundation for reusable team templates, task workflows, and provider-routing strategies.

## Product Demo

### Zero-account startup and Codex-first onboarding

![AgentClub onboarding](docs/images/agentclub-onboarding.jpg)

### CLI access and verification

![AgentClub CLI settings](docs/images/agentclub-cli-settings.jpg)

### Team routing configuration

![AgentClub team settings](docs/images/agentclub-team-settings.jpg)

## Quick Start

### Requirements

- macOS or a Linux shell environment
- Python 3.11+
- Node.js `20.19+` or `22.12+`
- npm

No real AI CLI is required for the first run.

### Start AgentClub

```bash
git clone https://github.com/Buaixihongshi/AgentClub.git
cd AgentClub
./scripts/start.sh
```

The script installs missing local dependencies, starts FastAPI and Vite, waits for the health check, and opens `http://127.0.0.1:5173/`. Press `Ctrl-C` to stop both services.

Useful options:

```bash
./scripts/start.sh --no-open
./scripts/start.sh --no-install
./scripts/start.sh --reset-onboarding
```

Use different ports when the defaults are occupied:

```bash
PORT=8001 FRONTEND_PORT=5174 ./scripts/start.sh --no-open
```

## Recommended First-Run Path

1. Run the first conversation in Demo Mode.
2. Install or sign in to Codex CLI and verify one real MCP conversation.
3. Let all three roles reuse the verified Codex connection.
4. Add Claude Code or Antigravity CLI when a task benefits from them.
5. Configure the primary provider and fallbacks for each role in Team Settings.

Real CLI verification sends one model request through the corresponding account and may consume plan quota or incur charges. Installation, login detection, version detection, and Demo Mode do not call a real model.

### Supported providers

| Provider | Command | Purpose | Default repository state |
| --- | --- | --- | --- |
| Demo Mode | Built in | Complete onboarding without an account | Enabled |
| Codex CLI | `codex` | Recommended first real provider; can power the full team | Disabled until verified |
| Claude Code | `claude` | Optional role provider | Disabled until verified |
| Antigravity CLI | `agy` | Optional role provider | Disabled until verified |

AgentClub never enables a real provider merely because the application started. Installation and login guidance is available in onboarding and Settings. A failed verification keeps the previously saved routing unchanged.

## How It Works

```text
React / Vite UI
       │ REST + SSE
       ▼
FastAPI ── MissionRunner ── Provider Router ── Local AI CLIs
   │              │                              │
   │              └──────── MCP tools ──────────┘
   ▼
SQLite event and conversation state + artifacts/<mission_id>/
```

- FastAPI creates a mission and runs role turns in a background workflow.
- The Provider Router selects Demo Mode or a real CLI and tries configured fallbacks.
- MCP carries Agent messages, context reads, artifacts, status updates, and Agent-to-Agent calls.
- The frontend consumes SSE snapshots and renders turns, progress, and diagnostics.

## Local Data and Privacy

AgentClub is local-first and does not provide a hosted account or remote data service. Runtime data is stored under:

| Path | Contents |
| --- | --- |
| `data/` | SQLite conversations, missions, and local settings |
| `artifacts/` | Agent-generated mission artifacts |
| `.yiren/` | Local startup logs and dependency stamps |

These directories are excluded from the public repository. Codex and Claude MCP settings use temporary isolated directories rather than permanently modifying global MCP configuration. Antigravity uses a guarded lifecycle with locking, restoration, and crash recovery.

Run the release privacy check before publishing a repository or attaching local files to an issue:

```bash
python3 scripts/privacy-check.py
```

## Development and Verification

Run the deterministic offline release gate:

```bash
./scripts/release-check.sh
```

It runs the backend and frontend test suites, creates a production build, and exercises onboarding, conversation continuity, and multi-Agent mission smoke flows without calling a paid model.

Real CLI verification is explicit because it requires authenticated CLIs and may consume model quota:

```bash
./scripts/release-check.sh --real-cli
```

## Current Scope

- AgentClub is currently a local, single-user MVP without hosted deployment, remote sync, or multi-user permissions.
- User interruptions are consumed between turns and do not forcibly terminate a CLI process that has already started.
- Real-provider availability depends on the installed CLI version, authentication state, and account quota.
- Agent artifacts should be written under `artifacts/<mission_id>/`; avoid using sensitive workspaces for real-model verification.

## Contributing

Issues, reproducible bug reports, and focused improvements are welcome. Before submitting a code change, run:

```bash
./scripts/release-check.sh
python3 scripts/privacy-check.py
```

See [CHANGELOG.md](CHANGELOG.md) for release notes and [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) for the current acceptance status.

## License

AgentClub is released under the [MIT License](LICENSE).
