#!/usr/bin/env python3
"""Real-CLI smoke verifying MCP publish_message against Antigravity CLI.

Targets the smallest possible MCP slice: one 王也 turn routed to the
``antigravity`` provider, expecting Antigravity CLI to call the AgentClub
``publish_message`` tool over MCP.

Usage:
    python3 scripts/smoke_mcp_antigravity.py
    python3 scripts/smoke_mcp_antigravity.py --timeout-seconds 240 --goal "用一句话自我介绍"
    python3 scripts/smoke_mcp_antigravity.py --turns 2 --timeout-seconds 240
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

from backend.app.adapters import antigravity_config as agc  # noqa: E402
from backend.app.main import create_app  # noqa: E402
from backend.app.settings import Settings  # noqa: E402

try:
    from fastapi.testclient import TestClient  # noqa: E402
except ImportError:  # pragma: no cover
    sys.exit("httpx / fastapi[testclient] not installed. Run: pip install httpx")


DEFAULT_GOAL = "用一句简短中文向老板汇报你正在做的工作。"
DEFAULT_TIMEOUT = 240
TARGET_AGENT = "wangye"
TARGET_PROVIDER = "antigravity"


def build_settings(tmp_dir: Path, cli_timeout: int, max_turns: int) -> Settings:
    config_dir = tmp_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    providers = json.loads(
        (PROJECT_ROOT / "config" / "cli-providers.json").read_text(encoding="utf-8")
    )
    for p in providers:
        p["enabled"] = p["id"] == TARGET_PROVIDER
        p["mcp_enabled"] = p["id"] == TARGET_PROVIDER
    (config_dir / "cli-providers.json").write_text(
        json.dumps(providers, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    agents_payload = json.loads(
        (PROJECT_ROOT / "config" / "agents.json").read_text(encoding="utf-8")
    )
    for agent in agents_payload["agents"]:
        if agent["id"] == TARGET_AGENT:
            agent["default_provider_id"] = TARGET_PROVIDER
            agent["fallback_provider_ids"] = []
    (config_dir / "agents.json").write_text(
        json.dumps(agents_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return Settings(
        database_path=tmp_dir / "yirenhub.sqlite3",
        artifact_root=tmp_dir / "artifacts",
        config_dir=config_dir,
        use_fake_cli=False,
        max_turns=max_turns,
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


def evaluate(detail: dict) -> dict:
    mission = detail["mission"]
    messages = detail.get("messages", [])
    turns = detail.get("turns", [])
    tool_events = detail.get("tool_events", [])
    agent_msgs = [m for m in messages if m.get("role") == "agent"]

    publish_calls = [
        e
        for e in tool_events
        if e.get("tool_name") == "publish_message"
        and e.get("agent_id") == TARGET_AGENT
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
            attempt_summaries.append(
                {
                    "turn_id": t.get("turn_id"),
                    "attempt": a.get("attempt"),
                    "provider_id": a.get("provider_id"),
                    "status": a.get("status"),
                    "summary": a.get("summary"),
                    "error": a.get("error"),
                }
            )

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
    parser = argparse.ArgumentParser(description="MCP smoke against Antigravity CLI")
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--turns", type=int, default=2, help="mission max_turns (>=2 for multi-turn)")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="yiren_mcp_antigravity_smoke_") as tmp:
        tmp_dir = Path(tmp)
        # D1 还原断言:运行前后对全局 mcp_config 做字节级快照比对。
        # 前提:运行期间无其他进程改写 ~/.gemini/config/mcp_config.json
        # (并发 smoke / 用户手动编辑会导致此断言误报)。
        global_cfg = agc.global_mcp_config_path()
        before_existed = global_cfg.exists()
        before_text = global_cfg.read_text(encoding="utf-8") if before_existed else None
        print("Temporary workspace: {ws}".format(ws=tmp))
        settings = build_settings(tmp_dir, cli_timeout=args.timeout_seconds, max_turns=args.turns)
        app = create_app(settings)
        client = TestClient(app)

        print("Creating mission (Antigravity × MCP, max_turns={n})...".format(n=args.turns))
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
        report = evaluate(detail)
        report["elapsed_seconds"] = round(elapsed, 1)
        after_existed = global_cfg.exists()
        after_text = global_cfg.read_text(encoding="utf-8") if after_existed else None
        config_restored = (before_existed == after_existed) and (before_text == after_text)
        report["global_config_restored"] = config_restored

        print("\n" + "=" * 60)
        print("MCP × Antigravity Smoke Report")
        print("=" * 60)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print("=" * 60)

        ok = (
            report["status"] == "completed"
            and report["via_mcp_turn_count"] >= 1
            and report["publish_message_tool_calls"] >= 1
            and report["agent_message_count"] >= 1
            and report["global_config_restored"]
        )
        if ok:
            print("\nPASS: Antigravity called publish_message via MCP and message landed in repository.")
            return 0
        print("\nFAIL: MCP path was not exercised end-to-end. See report above.")
        if report["status"] != "completed":
            print("  - mission did not complete (check Antigravity auth, MCP config, or timeout).")
        if report["via_mcp_turn_count"] == 0:
            print("  - no turn summary contained 'via MCP'.")
        if report["publish_message_tool_calls"] == 0:
            print("  - no publish_message tool.call event recorded.")
        if not report["global_config_restored"]:
            print("  - GLOBAL CONFIG NOT RESTORED: ~/.gemini/config/mcp_config.json differs from pre-run snapshot!")
        return 2


if __name__ == "__main__":
    sys.exit(main())
