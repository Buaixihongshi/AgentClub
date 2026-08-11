"""Fake CLI used by test_mcp_subprocess_adapter to simulate a real MCP-aware
provider (e.g. Codex). Spawns the AgentClub MCP server, performs the JSON-RPC
handshake, and calls one or more tools.

Reads its own behaviour from env vars set by MCPSubprocessAdapter:

  YIREN_FAKE_MCP_MODE = "publish" | "noop" | "envelope" | "exit_nonzero"
                      | "read" | "write" | "status" | "invoke"
  YIREN_FAKE_MCP_CONTENT = optional content for publish_message / artifact content
  YIREN_FAKE_MCP_TARGET = target_agent_id for invoke mode (default "baobao")
  YIREN_FAKE_MCP_PURPOSE = purpose for invoke mode (default "核对异闻物档案")
  YIREN_FAKE_MCP_INVOKE_COUNT = number of invoke_agent calls to make (default 1)
  YIREN_FAKE_MCP_PYTHON = python executable to spawn the server with (defaults to sys.executable)
  YIREN_FAKE_MCP_REPO_ROOT = repo root for PYTHONPATH (defaults to inferred)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path


def _spawn_server() -> subprocess.Popen:
    python = os.environ.get("YIREN_FAKE_MCP_PYTHON") or sys.executable
    repo_root = os.environ.get("YIREN_FAKE_MCP_REPO_ROOT") or str(
        Path(__file__).resolve().parents[2]
    )
    env = os.environ.copy()
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        config_path = Path(codex_home) / "config.toml"
        if config_path.is_file():
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            server = (config.get("mcp_servers") or {}).get("yiren_hub") or {}
            env.update({str(k): str(v) for k, v in (server.get("env") or {}).items()})
    env["PYTHONPATH"] = repo_root + (
        (os.pathsep + env["PYTHONPATH"]) if env.get("PYTHONPATH") else ""
    )
    return subprocess.Popen(
        [python, "-m", "backend.app.mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=repo_root,
    )


def _request(proc: subprocess.Popen, payload: dict) -> dict:
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    request_id = payload.get("id")
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("server closed stdout")
        frame = json.loads(line)
        if frame.get("id") == request_id:
            return frame


def _notify(proc: subprocess.Popen, payload: dict) -> None:
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def _handshake(proc: subprocess.Popen) -> None:
    _request(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "fake-cli", "version": "0.0.1"},
            },
        },
    )
    _notify(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})


def _call_tool(proc: subprocess.Popen, name: str, arguments: dict, *, request_id: int = 2) -> dict:
    return _request(
        proc,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )


def _close_server(proc: subprocess.Popen) -> None:
    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    mode = os.environ.get("YIREN_FAKE_MCP_MODE", "publish")
    if mode == "exit_nonzero":
        sys.stderr.write("fake cli failed\n")
        return 1

    if mode == "envelope":
        content = os.environ.get("YIREN_FAKE_MCP_CONTENT", "fallback envelope")
        sys.stdout.write(json.dumps({"message": content, "artifacts": []}))
        sys.stdout.flush()
        return 0

    if mode == "noop":
        return 0

    proc = _spawn_server()
    try:
        _handshake(proc)

        if mode == "read":
            # Call read_context and print the result to stdout.
            response = _call_tool(proc, "read_context", {"limit": 5})
            result = response.get("result") or {}
            if result.get("isError"):
                sys.stderr.write("read_context returned isError: " + json.dumps(result))
                return 3
            # Also publish a message so the turn completes via MCP.
            response = _call_tool(
                proc,
                "publish_message",
                {"content": os.environ.get("YIREN_FAKE_MCP_CONTENT", "read_context ok")},
                request_id=3,
            )
            result = response.get("result") or {}
            if result.get("isError"):
                sys.stderr.write("publish_message returned isError: " + json.dumps(result))
                return 3
            return 0

        if mode == "write":
            # Call write_artifact then publish a message.
            content = os.environ.get("YIREN_FAKE_MCP_CONTENT", "artifact content")
            response = _call_tool(
                proc,
                "write_artifact",
                {"relative_path": "test_output.txt", "content": content},
            )
            result = response.get("result") or {}
            if result.get("isError"):
                sys.stderr.write("write_artifact returned isError: " + json.dumps(result))
                return 3
            response = _call_tool(
                proc,
                "publish_message",
                {"content": "artifact written"},
                request_id=3,
            )
            result = response.get("result") or {}
            if result.get("isError"):
                sys.stderr.write("publish_message returned isError: " + json.dumps(result))
                return 3
            return 0

        if mode == "status":
            # Call update_status then publish a message.
            target_status = os.environ.get("YIREN_FAKE_MCP_STATUS", "completed")
            response = _call_tool(
                proc,
                "update_status",
                {"status": target_status, "summary": "status update via MCP"},
            )
            result = response.get("result") or {}
            if result.get("isError"):
                sys.stderr.write("update_status returned isError: " + json.dumps(result))
                return 3
            response = _call_tool(
                proc,
                "publish_message",
                {"content": "status updated to " + target_status},
                request_id=3,
            )
            result = response.get("result") or {}
            if result.get("isError"):
                sys.stderr.write("publish_message returned isError: " + json.dumps(result))
                return 3
            return 0

        if mode == "invoke":
            target = os.environ.get("YIREN_FAKE_MCP_TARGET", "baobao")
            purpose = os.environ.get("YIREN_FAKE_MCP_PURPOSE", "核对异闻物档案")
            invoke_count = int(os.environ.get("YIREN_FAKE_MCP_INVOKE_COUNT", "1"))
            for i in range(invoke_count):
                response = _call_tool(
                    proc,
                    "invoke_agent",
                    {"target_agent_id": target, "purpose": purpose},
                    request_id=2 + i,
                )
                result = response.get("result") or {}
                if result.get("isError"):
                    sys.stderr.write(
                        "invoke_agent returned isError: "
                        + json.dumps(result, ensure_ascii=False)
                    )
                    return 3
            response = _call_tool(
                proc,
                "publish_message",
                {"content": os.environ.get("YIREN_FAKE_MCP_CONTENT", "我已召唤接力。")},
                request_id=2 + invoke_count,
            )
            result = response.get("result") or {}
            if result.get("isError"):
                sys.stderr.write("publish_message returned isError: " + json.dumps(result, ensure_ascii=False))
                return 3
            return 0

        if mode == "usage":
            response = _call_tool(
                proc,
                "publish_message",
                {"content": os.environ.get("YIREN_FAKE_MCP_CONTENT", "usage test")},
            )
            result = response.get("result") or {}
            if result.get("isError"):
                sys.stderr.write("publish_message returned isError: " + json.dumps(result))
                return 3
            _close_server(proc)
            usage_json = os.environ.get(
                "YIREN_FAKE_MCP_USAGE_JSON",
                '{"input_tokens":100,"cached_input_tokens":10,"output_tokens":50,"reasoning_output_tokens":5}',
            )
            sys.stdout.write(json.dumps({"type": "turn.completed", "usage": json.loads(usage_json)}) + "\n")
            sys.stdout.flush()
            return 0

        # Default: publish exactly one message.
        response = _call_tool(
            proc,
            "publish_message",
            {"content": os.environ.get("YIREN_FAKE_MCP_CONTENT", "fake-cli MCP message")},
        )
        result = response.get("result") or {}
        if result.get("isError"):
            sys.stderr.write("publish_message returned isError: " + json.dumps(result))
            return 3
    finally:
        _close_server(proc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
