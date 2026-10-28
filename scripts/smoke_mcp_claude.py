#!/usr/bin/env python3
"""Real-CLI smoke verifying the Gamma G2-3 MCP handshake against Claude Code CLI.

Targets the smallest possible MCP slice: a single mission with
``target_agent_id="chulan"`` (张楚岚 → Claude by default), one turn, expecting
Claude to call the Unity ``publish_message`` tool over MCP rather than
printing to stdout.

Pass criteria:
  - mission status == "completed"
  - the only turn's summary contains "via MCP"
  - tool_events include {tool_name: "publish_message", agent_id: "chulan"}
  - at least one agent message exists

Requires Claude CLI on PATH and a valid Claude auth (``claude auth login`` or
``ANTHROPIC_API_KEY`` set).

Usage:
    python3 scripts/smoke_mcp_claude.py
    python3 scripts/smoke_mcp_claude.py --timeout-seconds 240 --goal "用一句话自我介绍"
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.main import create_app  # noqa: E402
from backend.app.settings import Settings  # noqa: E402

try:
    from fastapi.testclient import TestClient  # noqa: E402
except ImportError:  # pragma: no cover
    sys.exit("httpx / fastapi[testclient] not installed. Run: pip install httpx")


DEFAULT_GOAL = "用一句简短中文向老板汇报你正在做的工作。"
DEFAULT_TIMEOUT = 180
TARGET_AGENT = "chulan"


def build_settings(tmp_dir: Path, cli_timeout: int) -> Settings:
    config_dir = tmp_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    base_config = PROJECT_ROOT / "config" / "cli-providers.json"
    providers = json.loads(base_config.read_text(encoding="utf-8"))
    for p in providers:
        p["enabled"] = p["id"] == "claude"
        p["mcp_enabled"] = p["id"] == "claude"
    (config_dir / "cli-providers.json").write_text(
        json.dumps(providers, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return Settings(
        database_path=tmp_dir / "yirenhub.sqlite3",
        artifact_root=tmp_dir / "artifacts",
        config_dir=config_dir,
        use_fake_cli=False,
        max_turns=1,
        cli_timeout=cli_timeout,
    )


def wait_terminal(client, mission_id: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    detail = {}
    while time.monotonic() < deadline:
        resp = client.get("/api/missions/{mid}".format(mid=mission_id))
        resp.raise_for_status()
        detail = resp.json()
        status = detail["mission"]["status"]
        if status in ("completed", "failed", "blocked"):
            return detail
        time.sleep(0.5)
    return detail


def evaluate(detail: dict, target_agent: str) -> dict:
    mission = detail["mission"]
    messages = detail.get("messages", [])
    turns = detail.get("turns", [])
    tool_events = detail.get("tool_events", [])
    agent_msgs = [m for m in messages if m.get("role") == "agent"]

    publish_calls = [
        e for e in tool_events
        if e.get("tool_name") == "publish_message" and e.get("agent_id") == target_agent
    ]
    via_mcp_turns = [
        t
        for t in turns
        if "via MCP" in (t.get("summary") or "")
        or any("via MCP" in (a.get("summary") or "") for a in t.get("attempts", []))
    ]

    attempt_summaries = []
    for t in turns:
        for a in t.get("attempts", []):
            attempt_summaries.append({
                "turn_id": t.get("turn_id"),
                "attempt": a.get("attempt"),
                "provider_id": a.get("provider_id"),
                "status": a.get("status"),
                "summary": a.get("summary"),
                "error": a.get("error"),
            })

    return {
        "mission_id": mission["id"],
        "status": mission["status"],
        "agent_message_count": len(agent_msgs),
        "agent_message_preview": (agent_msgs[0]["content"][:80] if agent_msgs else None),
        "publish_message_tool_calls": len(publish_calls),
        "via_mcp_turn_count": len(via_mcp_turns),
        "turn_summaries": [t.get("summary") for t in turns],
        "attempt_details": attempt_summaries,
        "agent_message_breakdown": [
            {
                "turn_id": m.get("turn_id"),
                "agent_id": m.get("agent_id"),
                "provider_id": m.get("provider_id"),
                "content_preview": (m.get("content") or "")[:80],
            }
            for m in agent_msgs
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gamma G2-3 MCP smoke against Claude CLI")
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="yiren_mcp_claude_smoke_") as tmp:
        tmp_dir = Path(tmp)
        print("Temporary workspace: {ws}".format(ws=tmp))
        settings = build_settings(tmp_dir, cli_timeout=args.timeout_seconds)
        app = create_app(settings)
        client = TestClient(app)

        print("Creating mission (Claude × MCP, max_turns=1)...")
        t0 = time.monotonic()
        resp = client.post(
            "/api/missions",
            json={"goal": args.goal, "target_agent_id": TARGET_AGENT},
        )
        if resp.status_code != 201:
            print("ERROR: create mission returned {code}".format(code=resp.status_code))
            print(resp.text)
            return 1
        mission_id = resp.json()["mission"]["id"]
        print("Mission: {mid}".format(mid=mission_id))
        print("Waiting for terminal status (timeout={t}s)...".format(t=args.timeout_seconds))

        detail = wait_terminal(client, mission_id, args.timeout_seconds)
        elapsed = time.monotonic() - t0
        report = evaluate(detail, TARGET_AGENT)
        report["elapsed_seconds"] = round(elapsed, 1)

        print("\n" + "=" * 60)
        print("Gamma G2-3 MCP × Claude Smoke Report")
        print("=" * 60)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print("=" * 60)

        ok = (
            report["status"] == "completed"
            and report["via_mcp_turn_count"] >= 1
            and report["publish_message_tool_calls"] >= 1
            and report["agent_message_count"] >= 1
        )
        if ok:
            print("\nPASS: Claude called publish_message via MCP and message landed in repository.")
            return 0
        print("\nFAIL: MCP path was not exercised end-to-end. See report above.")
        if report["status"] != "completed":
            print("  - mission did not complete (check Claude auth or timeout).")
        if report["via_mcp_turn_count"] == 0:
            print("  - no turn summary contained 'via MCP' (Claude may have ignored the MCP tool).")
        if report["publish_message_tool_calls"] == 0:
            print("  - no publish_message tool.call event recorded.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
