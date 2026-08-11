"""Adapter that runs a CLI provider as an MCP client of the AgentClub MCP server.

Per-provider config rendering:
- codex: materialises a TOML at ``<CODEX_HOME>/config.toml`` and sets
  ``CODEX_HOME`` to an ephemeral tmpdir that is seeded with codex auth files.
- claude: writes an MCP JSON config file and passes ``--mcp-config <path>
  --strict-mcp-config`` flags so the server is injected without touching
  ``~/.claude``.
- gemini: writes a ``settings.json`` under ``<HOME>/.gemini/`` in an
  ephemeral tmpdir and sets ``HOME`` so that Gemini reads our MCP
  server without touching ``~/.gemini``.
- agy: Antigravity CLI 1.0.2 reads MCP only from the global
  ``~/.gemini/config/mcp_config.json``; we guard-mutate-restore it for the
  subprocess lifetime (see ``antigravity_config.guarded_global_mcp_config``)
  and run from a throwaway workspace cwd.

After the CLI exits we inspect the repository event log: if the agent message
was published via MCP we mark the turn ``COMPLETED`` with summary ``"via MCP"``;
otherwise the turn is marked ``BLOCKED`` with summary
``"agent did not call publish_message via MCP"`` (ADR Decision 5 — stdout is
never synthesised into an agent message for MCP-enabled providers).
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional
from uuid import uuid4

from backend.app.adapters.antigravity_config import (
    guarded_global_mcp_config,
    read_agy_log_hint,
)
from backend.app.adapters.base import CLIAdapter, TurnOutcome
from backend.app.adapters.stream_events import StreamEventType, UsageRecord
from backend.app.adapters.stream_parsers import StreamLineParser
from backend.app.adapters.subprocess_cli import (
    _IS_WINDOWS,
    build_failure_summary,
    kill_process_tree,
)
from backend.app.adapters.subprocess_cli import _parse_result_envelope
from backend.app.domain.models import TurnStatus, is_provider_verification_mission
from backend.app.mcp.tools import MCPToolError, MCPTools
from backend.app.orchestration.live_progress import (
    LiveProgressRegistry,
    get_live_progress_registry,
)
from backend.app.storage.repository import Repository


MCP_SERVER_NAME = "yiren_hub"


class MCPSubprocessAdapter(CLIAdapter):
    def __init__(
        self,
        command: str,
        args: Optional[List[str]] = None,
        *,
        db_path: Path,
        artifact_root: Path,
        mcp_python: Optional[str] = None,
        mcp_module: str = "backend.app.mcp.server",
        timeout: int = 120,
        extra_env: Optional[dict] = None,
        on_config_written: Optional[Callable[[Path], None]] = None,
        live_progress: Optional[LiveProgressRegistry] = None,
        gemini_home_override: Optional[Path] = None,
        gemini_resume_session_id: Optional[str] = None,
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.db_path = Path(db_path).resolve()
        self.artifact_root = Path(artifact_root).resolve()
        self.mcp_python = mcp_python or sys.executable
        self.mcp_module = mcp_module
        self.timeout = timeout
        self.extra_env = dict(extra_env or {})
        self.on_config_written = on_config_written
        self.live_progress = live_progress or get_live_progress_registry()
        self.gemini_home_override = Path(gemini_home_override) if gemini_home_override else None
        self.gemini_resume_session_id = gemini_resume_session_id

    def run_turn(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        prompt: str,
        tools: MCPTools,
    ) -> TurnOutcome:
        outcome = self._run_attempt(
            mission_id, agent_id, turn_id, provider_id, prompt, tools,
            resume_session_id=self.gemini_resume_session_id,
        )
        # Plan A: if Gemini rejected our --resume target (its session file was
        # not flushed cleanly last turn, e.g. final result.status=error), retry
        # once in the same run_turn without --resume so the agent recovers via
        # cold start instead of propagating a hard BLOCKED to the runner.
        if (
            self.gemini_resume_session_id is not None
            and outcome.status == TurnStatus.BLOCKED
            and "Invalid session identifier" in (outcome.summary or "")
        ):
            outcome = self._run_attempt(
                mission_id, agent_id, turn_id, provider_id, prompt, tools,
                resume_session_id=None,
            )
        return outcome

    def _run_attempt(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        prompt: str,
        tools: MCPTools,
        *,
        resume_session_id: Optional[str],
    ) -> TurnOutcome:
        repository = tools.repository
        cursor = _max_event_id(repository, mission_id)

        with tempfile.TemporaryDirectory(prefix="yiren-mcp-") as tmpdir, \
                contextlib.ExitStack() as guard_stack:
            tmp = Path(tmpdir)
            mcp_env = self._mcp_env(
                mission_id,
                agent_id,
                turn_id,
                provider_id,
                valid_agent_ids=tools.valid_agent_ids,
                max_a2a_invocations=tools.max_a2a_invocations,
            )
            receipt_id = "mcp_{suffix}".format(suffix=uuid4().hex)
            mcp_env["YIREN_MCP_RECEIPT_ID"] = receipt_id
            mcp_prompt = _augment_prompt_for_mcp(prompt)
            launch_env = os.environ.copy()
            launch_env.update(self.extra_env)
            # Runtime routing data belongs to the MCP server process only. It
            # must not leak into the provider CLI where it could be used to
            # bypass stdio MCP and write directly to AgentClub persistence.
            for key in list(launch_env):
                if key.startswith("YIREN_MCP_"):
                    launch_env.pop(key, None)

            cli_basename = os.path.basename(self.command)
            mission = repository.get_mission(mission_id)
            is_verification = bool(
                mission and is_provider_verification_mission(mission)
            )

            cli_cwd: Optional[Path] = None
            if is_verification:
                cli_cwd = tmp / "verification-workspace"
                cli_cwd.mkdir(parents=True, exist_ok=True)
            agy_run_start: Optional[float] = None  # set in agy branch below
            if cli_basename == "claude":
                cmd, config_path = self._setup_claude(tmp, mcp_env, mcp_prompt)
            elif cli_basename == "gemini":
                gemini_home = self.gemini_home_override or tmp
                if self.gemini_home_override is not None:
                    self.gemini_home_override.mkdir(parents=True, exist_ok=True)
                cmd, config_path = self._setup_gemini(
                    gemini_home, mcp_env, mcp_prompt, launch_env,
                    resume_session_id=resume_session_id,
                )
            elif cli_basename == "agy":
                cmd, config_path, cli_cwd, agy_server_spec = self._setup_antigravity(
                    tmp,
                    mcp_env,
                    mcp_prompt,
                    include_repo_context=not is_verification,
                )
                # Inject yiren_hub into the global ~/.gemini/config/mcp_config.json
                # for the agy subprocess lifetime; released (restored) when the
                # ExitStack (guard_stack) unwinds at the end of this `with`, after
                # the subprocess has fully finished.
                guard_stack.enter_context(guarded_global_mcp_config(agy_server_spec))
                # Captured so read_agy_log_hint can filter to logs written during
                # this run only. agy 1.0.3 swallows fatal conditions (no auth /
                # Google quota 429) into silent exit 0, so we mine its own log
                # for context to enrich the BLOCKED summary below.
                agy_run_start = time.time()
            else:
                # Default: codex-style TOML config.
                config_path = tmp / "config.toml"
                config_path.write_text(
                    self._render_codex_config(
                        mcp_env,
                        project_path=cli_cwd or Path.cwd(),
                    ),
                    encoding="utf-8",
                )
                _seed_codex_auth(tmp)
                launch_env["CODEX_HOME"] = str(tmp)
                cmd = [self.command, *self.args, mcp_prompt]

            if self.on_config_written is not None:
                self.on_config_written(config_path)

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    start_new_session=not _IS_WINDOWS,
                    env=launch_env,
                    cwd=str(cli_cwd) if cli_cwd is not None else None,
                )
            except OSError as exc:
                return TurnOutcome(
                    status=TurnStatus.BLOCKED,
                    summary=build_failure_summary(provider_id, os_error=str(exc)),
                )

            self.live_progress.mark_active(mission_id, turn_id)

            def _augment_agy_summary(summary: str) -> str:
                """Mine the most recent agy log for a quota/auth hint and append
                it to the BLOCKED summary. No-op for non-agy providers and when
                the log yields nothing recognisable."""
                if cli_basename != "agy" or agy_run_start is None:
                    return summary
                hint = read_agy_log_hint(agy_run_start)
                return f"{summary} | {hint}" if hint else summary

            stdout_lines: list[str] = []
            stderr_lines: list[str] = []
            last_usage: list[Optional[UsageRecord]] = [None]
            captured_session_id: list[Optional[str]] = [None]
            stream_ended_with_error: list[bool] = [False]
            stream_parser = StreamLineParser(provider_id)

            def _drain_stdout() -> None:
                if proc.stdout is None:
                    return
                try:
                    for raw in iter(proc.stdout.readline, ""):
                        if not raw or not isinstance(raw, str):
                            break
                        line = raw.rstrip("\n")
                        stdout_lines.append(line)
                        ev = stream_parser.parse(line)
                        if ev is None:
                            continue
                        if ev.type is StreamEventType.PARTIAL_TEXT and ev.text:
                            self.live_progress.append_text(mission_id, turn_id, ev.text)
                        if ev.type is StreamEventType.SESSION_STARTED and ev.session_id:
                            captured_session_id[0] = ev.session_id
                        if ev.type is StreamEventType.ERROR:
                            stream_ended_with_error[0] = True
                        if ev.type is StreamEventType.FINISHED:
                            stream_ended_with_error[0] = False
                        if ev.usage is not None:
                            last_usage[0] = ev.usage
                except Exception:
                    pass

            def _drain_stderr() -> None:
                if proc.stderr is None:
                    return
                try:
                    for raw in iter(proc.stderr.readline, ""):
                        if not raw or not isinstance(raw, str):
                            break
                        stderr_lines.append(raw.rstrip("\n"))
                except Exception:
                    pass

            t_out = threading.Thread(target=_drain_stdout, daemon=True)
            t_err = threading.Thread(target=_drain_stderr, daemon=True)
            t_out.start()
            t_err.start()

            try:
                try:
                    proc.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    kill_process_tree(proc)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    t_out.join(timeout=2)
                    t_err.join(timeout=2)
                    stdout = "\n".join(stdout_lines)
                    stderr = "\n".join(stderr_lines)
                    _write_debug_files(turn_id, provider_id, stdout, stderr, config_path)
                    # Plan B: a final result.status=error means the on-disk session
                    # was likely not persisted — do not surface that sid upstream.
                    if stream_ended_with_error[0]:
                        captured_session_id[0] = None
                    return TurnOutcome(
                        status=TurnStatus.BLOCKED,
                        summary=_augment_agy_summary(build_failure_summary(
                            provider_id,
                            timeout=self.timeout,
                            stderr_snippet=stderr.strip(),
                            stdout_snippet=stdout.strip(),
                        )),
                        session_id=captured_session_id[0],
                    )

                t_out.join(timeout=2)
                t_err.join(timeout=2)
                stdout = "\n".join(stdout_lines).strip()
                stderr = "\n".join(stderr_lines).strip()

                _write_debug_files(turn_id, provider_id, stdout, stderr, config_path)

                if stream_ended_with_error[0]:
                    captured_session_id[0] = None

                if proc.returncode != 0:
                    return TurnOutcome(
                        status=TurnStatus.BLOCKED,
                        summary=_augment_agy_summary(build_failure_summary(
                            provider_id,
                            exit_code=proc.returncode,
                            stderr_snippet=stderr,
                            stdout_snippet=stdout,
                        )),
                        session_id=captured_session_id[0],
                    )

                if _mcp_published_message(
                    repository,
                    mission_id,
                    turn_id,
                    cursor,
                    receipt_id,
                    agent_id,
                    provider_id,
                ):
                    _emit_turn_usage(repository, mission_id, agent_id, provider_id, turn_id, last_usage[0])
                    return TurnOutcome(
                        status=TurnStatus.COMPLETED,
                        summary="via MCP",
                        session_id=captured_session_id[0],
                    )

                # D6: agy can exit 0 after its own --print-timeout fires without
                # ever calling publish_message. Give a timeout-flavoured BLOCK so
                # the runner's fallback chain reads cleanly. The literal matches
                # agy 1.0.2's --print-timeout output ("timed out waiting for
                # response"); if a future release changes it, this falls through
                # to the generic BLOCK below, which is still correct.
                if cli_basename == "agy" and "timed out waiting for response" in stdout.lower():
                    return TurnOutcome(
                        status=TurnStatus.BLOCKED,
                        summary=_augment_agy_summary(build_failure_summary(
                            provider_id,
                            timeout=self.timeout,
                            stdout_snippet=stdout,
                            stderr_snippet=stderr,
                        )),
                        session_id=captured_session_id[0],
                    )

                # ADR Decision 5: MCP-enabled providers must call publish_message via MCP.
                # stdout is never synthesised into an agent message when mcp_enabled=true.
                return TurnOutcome(
                    status=TurnStatus.BLOCKED,
                    summary=_augment_agy_summary("agent did not call publish_message via MCP"),
                    session_id=captured_session_id[0],
                )
            finally:
                self.live_progress.clear(mission_id, turn_id)

    def _setup_claude(
        self, tmp: Path, mcp_env: dict, mcp_prompt: str
    ) -> tuple:
        """Write an MCP JSON config file and return (cmd, config_path).

        Uses ``--mcp-config <path> --strict-mcp-config`` so that no MCP
        servers from the user's global ~/.claude are loaded — ephemeral and
        non-polluting.  Auth is handled by claude itself (keychain /
        ANTHROPIC_API_KEY), so we do not need to seed credentials.
        """
        config_path = tmp / "mcp.json"
        config_path.write_text(
            self._render_claude_mcp_config(mcp_env), encoding="utf-8"
        )
        cmd = [
            self.command,
            "--mcp-config", str(config_path),
            "--strict-mcp-config",
            *self.args,
            mcp_prompt,
        ]
        return cmd, config_path

    def _setup_gemini(
        self, gemini_home: Path, mcp_env: dict, mcp_prompt: str, launch_env: dict,
        *, resume_session_id: Optional[str] = None,
    ) -> tuple:
        """Inject MCP into Gemini via ``GEMINI_CLI_HOME``.

        Gemini CLI reads ``$GEMINI_CLI_HOME/.gemini/settings.json`` when the
        env var is set (otherwise ``~/.gemini``); pointing it at a tempdir
        injects our MCP server without touching the user's real ``~/.gemini``.
        Vertex-AI / API-key auth uses ambient credentials (gcloud config,
        env vars) so redirecting only the Gemini home does not break login.
        OAuth-personal credential files are copied in for completeness.

        Adds ``--skip-trust`` because the workspace is the runtime cwd, not
        a directory the user has interactively trusted. When
        ``resume_session_id`` is supplied, ``--resume <uuid>`` is injected so
        gemini reattaches to the previously-written session file under
        ``$GEMINI_CLI_HOME/.gemini/tmp/<bucket>/chats/``.
        """
        gemini_dir = gemini_home / ".gemini"
        gemini_dir.mkdir(parents=True, exist_ok=True)
        settings_path = gemini_dir / "settings.json"
        _seed_gemini_auth(gemini_home)
        settings_path.write_text(
            self._render_gemini_settings(mcp_env, settings_path=settings_path), encoding="utf-8"
        )
        launch_env["GEMINI_CLI_HOME"] = str(gemini_home)
        resume_args: list = ["--resume", resume_session_id] if resume_session_id else []
        cmd = [
            self.command,
            "--skip-trust",
            *resume_args,
            *self.args,
            _compact_prompt_for_gemini_mcp(mcp_prompt),
        ]
        return cmd, settings_path

    def _setup_antigravity(
        self,
        tmp: Path,
        mcp_env: dict,
        mcp_prompt: str,
        *,
        include_repo_context: bool = True,
    ) -> tuple:
        """Prepare the agy command + a server_spec for the global-config guard.

        Antigravity CLI 1.0.2 reads MCP servers ONLY from the global
        ``~/.gemini/config/mcp_config.json`` (it ignores GEMINI_CLI_HOME /
        XDG_CONFIG_HOME / --app_data_dir, has no --mcp-config flag, and
        ``.agents/`` is agent metadata only). So we do NOT write any config
        here — the caller injects ``server_spec`` into the global file via
        ``guarded_global_mcp_config`` for the agy subprocess lifetime. We run
        from a throwaway workspace so agy's ``.antigravitycli/`` project
        registration lands in tmp, not the repo. ``--add-dir <repo>`` gives agy
        the repo as read-only context; ``--print-timeout`` aligns agy's own
        timeout just under our hard kill so it self-terminates gracefully.

        Returns ``(cmd, debug_config_path, workspace_cwd, server_spec)``.
        """
        workspace = tmp / "antigravity-workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        server_spec = self._antigravity_server_spec(mcp_env)

        # Observability-only copy of what we inject globally (never read by agy).
        debug_config_path = workspace / "yiren_mcp_config.debug.json"
        debug_config_path.write_text(
            json.dumps(
                {"mcpServers": {MCP_SERVER_NAME: server_spec}},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        cmd_args = list(self.args)
        if "--dangerously-skip-permissions" not in cmd_args:
            cmd_args.insert(0, "--dangerously-skip-permissions")
        if not any(arg in ("-p", "--print", "--prompt") for arg in cmd_args):
            cmd_args.append("-p")
        if (
            include_repo_context
            and not any(a == "--add-dir" or a.startswith("--add-dir=") for a in cmd_args)
        ):
            cmd_args[0:0] = ["--add-dir", os.getcwd()]
        # D6: let agy self-timeout ~10s before our hard kill (cli_timeout).
        if not any(a == "--print-timeout" or a.startswith("--print-timeout=") for a in cmd_args):
            agy_timeout = max(30, self.timeout - 10)
            cmd_args[0:0] = ["--print-timeout", "{n}s".format(n=agy_timeout)]

        cmd = [self.command, *cmd_args, mcp_prompt]
        return cmd, debug_config_path, workspace, server_spec

    def _mcp_env(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        valid_agent_ids: "Optional[set]" = None,
        max_a2a_invocations: int = 0,
    ) -> dict:
        env: dict = {
            "YIREN_MCP_DB_PATH": str(self.db_path),
            "YIREN_MCP_ARTIFACT_ROOT": str(self.artifact_root),
            "YIREN_MCP_MISSION_ID": mission_id,
            "YIREN_MCP_AGENT_ID": agent_id,
            "YIREN_MCP_TURN_ID": turn_id,
            "YIREN_MCP_PROVIDER_ID": provider_id,
        }
        if valid_agent_ids:
            env["YIREN_MCP_VALID_AGENTS"] = ",".join(sorted(valid_agent_ids))
        if max_a2a_invocations > 0:
            env["YIREN_MCP_MAX_A2A_INVOCATIONS"] = str(max_a2a_invocations)
        return env

    def _render_claude_mcp_config(self, env: dict) -> str:
        """Return a JSON string for ``--mcp-config`` consumed by claude CLI.

        Format: ``{"mcpServers": {"yiren_hub": {"command": ..., "args": [...], "env": {...}}}}``
        """
        server = {
            "command": self.mcp_python,
            "args": ["-m", self.mcp_module],
            "env": {k: str(v) for k, v in env.items()},
        }
        return json.dumps(
            {"mcpServers": {MCP_SERVER_NAME: server}},
            ensure_ascii=False,
            indent=2,
        )

    def _render_gemini_settings(self, env: dict, *, settings_path: Optional[Path] = None) -> str:
        """Return a JSON string for Gemini's settings.json.

        Reads the seeded settings.json (a copy of the user's real one) but
        keeps only auth-related and model-selection fields.  ``mcpServers``
        is replaced entirely with our injected entry — the user's global
        MCP server registry must not leak into agent turns.
        """
        settings: dict = {}
        if settings_path is not None and settings_path.is_file():
            try:
                loaded = json.loads(settings_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    for key in ("security", "model"):
                        if key in loaded:
                            settings[key] = loaded[key]
            except json.JSONDecodeError:
                settings = {}
        server = {
            "command": self.mcp_python,
            "args": ["-m", self.mcp_module],
            "env": {k: str(v) for k, v in env.items()},
        }
        settings["mcpServers"] = {MCP_SERVER_NAME: server}
        return json.dumps(settings, ensure_ascii=False, indent=2)

    def _antigravity_server_spec(self, env: dict) -> dict:
        """Return the inner MCP server dict injected into the global config."""
        server_env = {k: str(v) for k, v in env.items()}
        python_path_parts = [os.getcwd()]
        existing_pythonpath = os.environ.get("PYTHONPATH")
        if existing_pythonpath:
            python_path_parts.append(existing_pythonpath)
        server_env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
        return {
            "command": self.mcp_python,
            "args": ["-m", self.mcp_module],
            "env": server_env,
        }

    def _render_codex_config(
        self,
        env: dict,
        *,
        project_path: Optional[Path] = None,
    ) -> str:
        # Codex CLI reads MCP servers from a TOML config block. We render only
        # what we need; codex tolerates additional unknown blocks.
        # Trust the current working directory: without an explicit project
        # trust entry, non-interactive `codex exec` rejects every MCP tool
        # call as "user cancelled MCP tool call".
        lines = [
            '[projects.{path}]'.format(
                path=_toml_string(str(project_path or Path.cwd()))
            ),
            'trust_level = "trusted"',
            "",
            "[mcp_servers.{name}]".format(name=MCP_SERVER_NAME),
        ]
        lines.append("command = {value}".format(value=_toml_string(self.mcp_python)))
        lines.append(
            "args = [{value}]".format(value=", ".join([_toml_string("-m"), _toml_string(self.mcp_module)]))
        )
        lines.append("[mcp_servers.{name}.env]".format(name=MCP_SERVER_NAME))
        for key, value in env.items():
            lines.append("{key} = {value}".format(key=key, value=_toml_string(str(value))))
        lines.append("")
        return "\n".join(lines)

    def _fallback_via_stdout(
        self,
        tools: MCPTools,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        stdout: str,
    ) -> TurnOutcome:
        envelope = _parse_result_envelope(stdout) if stdout else None
        if envelope is not None:
            message = envelope.message.strip() or "(CLI returned no output)"
            tools.publish_message(mission_id, agent_id, turn_id, message, provider_id)
            for artifact in envelope.artifacts:
                try:
                    tools.write_artifact(
                        mission_id,
                        agent_id,
                        turn_id,
                        artifact.path,
                        artifact.content,
                        provider_id=provider_id,
                    )
                except MCPToolError as exc:
                    return TurnOutcome(status=TurnStatus.BLOCKED, summary=str(exc))
            return TurnOutcome(status=TurnStatus.COMPLETED, summary="stdout envelope")

        text = stdout.strip() or "(CLI returned no output)"
        tools.publish_message(mission_id, agent_id, turn_id, text, provider_id)
        return TurnOutcome(status=TurnStatus.COMPLETED, summary="stdout")


_MCP_PROMPT_GUIDANCE = (
    "\n【硬性输出协议】本会话已通过 MCP 注册 yiren_hub.publish_message 工具："
    "参数为 content (中文文本)。你本回合必须调用该 MCP 工具一次来发声；"
    "只要没有成功调用 yiren_hub.publish_message，本回合会被系统判定为未完成。"
    "不要只在 stdout/普通文本中说你已经发声。"
)


def _augment_prompt_for_mcp(prompt: str) -> str:
    return prompt + "\n" + _MCP_PROMPT_GUIDANCE


def _compact_prompt_for_gemini_mcp(prompt: str) -> str:
    """Keep Gemini MCP prompts small while preserving routing-critical context."""
    body = prompt.replace(_MCP_PROMPT_GUIDANCE, "").strip()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    compact: list[str] = []
    recent_lines: list[str] = []
    in_recent = False
    saw_a2a_instruction = any("invoke_agent" in line for line in lines)

    for line in lines:
        if line.startswith("最近对话："):
            in_recent = True
            continue
        if line.startswith("Mission:"):
            in_recent = False

        if in_recent:
            recent_lines.append(_truncate_text(line, 120))
            continue

        if line.startswith(
            (
                "聊天室模式",
                "你的角色是",
                "只回复当前角色",
                "不要读取文件",
                "Mission:",
                "Goal:",
                "Turn purpose:",
                "【接续上一轮】",
                "对方的回复",
                "请直接基于上面的对话历史回答",
            )
        ) or line.startswith("> "):
            compact.append(_truncate_text(line, 320))

    if recent_lines:
        insert_at = min(4, len(compact))
        compact[insert_at:insert_at] = ["最近对话（压缩）：", *recent_lines[-4:]]

    if saw_a2a_instruction:
        compact.append(
            '如需接力，可调用 invoke_agent(target, purpose, mode="wait_reply")；否则本轮只调用 publish_message。'
        )

    compact.append(_MCP_PROMPT_GUIDANCE.strip())
    return "\n".join(compact)


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _write_debug_files(
    turn_id: str,
    provider_id: str,
    stdout: str,
    stderr: str,
    config_path: Path,
) -> None:
    debug_dir = os.environ.get("YIREN_MCP_DEBUG_DIR")
    if not debug_dir:
        return
    debug_path = Path(debug_dir)
    debug_path.mkdir(parents=True, exist_ok=True)
    provider_stem = "{tid}.{provider}".format(tid=turn_id, provider=provider_id)
    for stem in (provider_stem, turn_id):
        (debug_path / "{stem}.stdout".format(stem=stem)).write_text(stdout, encoding="utf-8")
        (debug_path / "{stem}.stderr".format(stem=stem)).write_text(stderr, encoding="utf-8")
        try:
            (debug_path / "{stem}.config".format(stem=stem)).write_text(
                config_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        except Exception:
            pass


def _seed_codex_auth(codex_home: Path) -> None:
    real_home = Path(os.path.expanduser("~/.codex"))
    for name in ("auth.json", "config.json"):
        src = real_home / name
        if src.is_file():
            try:
                shutil.copy2(src, codex_home / name)
            except OSError:
                pass


def _seed_gemini_auth(gemini_home: Path) -> None:
    """Copy Gemini auth files from the real ~/.gemini into the ephemeral home.

    Copies only the minimal credential files so the CLI can authenticate
    without reading the user's full ~/.gemini directory.
    """
    real_gemini = Path(os.path.expanduser("~/.gemini"))
    dest_dir = gemini_home / ".gemini"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "settings.json",
        "google_accounts.json",
        "state.json",
        "trustedFolders.json",
        "installation_id",
        "credentials.json",
        "oauth_credentials.json",
        ".google-credentials.json",
    ):
        src = real_gemini / name
        if src.is_file():
            try:
                shutil.copy2(src, dest_dir / name)
            except OSError:
                pass


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"{v}"'.format(v=escaped)


def _max_event_id(repository: Repository, mission_id: str) -> int:
    events = repository.list_events_after(mission_id, 0)
    if not events:
        return 0
    return max(int(event.id) for event in events)


def _emit_turn_usage(
    repository: Repository,
    mission_id: str,
    agent_id: str,
    provider_id: str,
    turn_id: str,
    usage: Optional[UsageRecord],
) -> None:
    if usage is None:
        return
    repository.add_event(mission_id, "turn.usage", {
        "agent_id": agent_id,
        "provider_id": provider_id,
        "turn_id": turn_id,
        "usage": dataclasses.asdict(usage),
    })


def _mcp_published_message(
    repository: Repository,
    mission_id: str,
    turn_id: str,
    cursor: int,
    receipt_id: str,
    agent_id: str,
    provider_id: str,
) -> bool:
    new_events: Iterable = repository.list_events_after(mission_id, cursor)
    messages_by_id = {
        event.payload.get("id"): event.payload
        for event in new_events
        if event.event_type == "message"
        and (event.payload or {}).get("role") == "agent"
        and (event.payload or {}).get("turn_id") == turn_id
        and (event.payload or {}).get("agent_id") == agent_id
        and (event.payload or {}).get("provider_id") == provider_id
    }
    new_events = repository.list_events_after(mission_id, cursor)
    for event in new_events:
        if event.event_type != "mcp.receipt":
            continue
        payload = event.payload or {}
        if payload.get("receipt_id") != receipt_id:
            continue
        if (
            payload.get("turn_id") == turn_id
            and payload.get("agent_id") == agent_id
            and payload.get("provider_id") == provider_id
            and payload.get("tool") == "publish_message"
            and payload.get("message_id") in messages_by_id
        ):
            return True
    return False
