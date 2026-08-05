#!/usr/bin/env python3
"""YiRen Hub smoke acceptance script.

Usage:
    python3 scripts/smoke_real_cli_mission.py --fake
    python3 scripts/smoke_real_cli_mission.py --real
    python3 scripts/smoke_real_cli_mission.py --real --goal "讨论系统设计" --max-turns 2
"""

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
except ImportError:
    sys.exit("httpx / fastapi[testclient] not installed. Run: pip install httpx")

DEFAULT_GOAL = (
    "系统 后端 前端 测试 MCP 多 Agent。"
    "三位角色各用一段简短中文对话，讨论真实 CLI 聊天体验。"
)
DEFAULT_MAX_TURNS = 6
DEFAULT_TIMEOUT = 180


def build_settings(
    mode: str,
    tmp_dir: Path,
    max_turns: int,
    cli_timeout: int = 120,
) -> Settings:
    if mode == "fake":
        return Settings(
            database_path=tmp_dir / "yirenhub.sqlite3",
            artifact_root=tmp_dir / "artifacts",
            config_dir=Path("config"),
            use_fake_cli=True,
            max_turns=max_turns,
            cli_timeout=cli_timeout,
        )

    config_dir = tmp_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    base_config = PROJECT_ROOT / "config" / "cli-providers.json"
    providers = json.loads(base_config.read_text(encoding="utf-8"))
    for p in providers:
        if p["id"] == "codex":
            p["enabled"] = True
        elif p["id"] == "claude":
            p["enabled"] = False
        elif p["id"] == "fake":
            p["enabled"] = False
        # This script is the stdout-path regression baseline; the MCP path is
        # exercised by scripts/smoke_mcp_codex.py.
        p["mcp_enabled"] = False
    (config_dir / "cli-providers.json").write_text(
        json.dumps(providers, indent=2, ensure_ascii=False), encoding="utf-8"
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
    while time.monotonic() < deadline:
        resp = client.get("/api/missions/{mid}".format(mid=mission_id))
        resp.raise_for_status()
        detail = resp.json()
        status = detail["mission"]["status"]
        if status in ("completed", "failed", "blocked"):
            return detail
        time.sleep(0.5)
    return detail


def print_report(detail: dict, mode: str, elapsed_s: float, workspace: str) -> dict:
    """Print human-readable report and return JSON summary dict."""
    mission = detail["mission"]
    messages = detail.get("messages", [])
    turns = detail.get("turns", [])
    tool_events = detail.get("tool_events", [])
    artifacts = detail.get("artifacts", [])
    interrupts = detail.get("interrupts", [])

    agent_msgs = [m for m in messages if m.get("role") == "agent"]
    providers_used = sorted(set(m.get("provider_id", "?") for m in agent_msgs))

    print("\n" + "=" * 60)
    print("YiRen Hub Smoke Acceptance Report")
    print("=" * 60)
    print("Mode:            {mode}".format(mode=mode))
    print("Mission ID:      {mid}".format(mid=mission["id"]))
    print("Status:          {st}".format(st=mission["status"]))
    print("Complexity:      {cx}".format(cx=mission.get("complexity", "?")))
    print("Elapsed:         {t:.1f}s".format(t=elapsed_s))
    print("Workspace:       {ws}".format(ws=workspace))
    print("Cleanup:         automatic (TemporaryDirectory)")
    print("")

    print("--- Agent Messages ({n}) ---".format(n=len(agent_msgs)))
    for msg in agent_msgs:
        agent = msg.get("agent_id", "?")
        provider = msg.get("provider_id", "?")
        turn_id = msg.get("turn_id", "?")
        content = (msg.get("content") or "")[:120]
        print("  [{agent}] via {prov} (turn={tid}): {txt}".format(
            agent=agent, prov=provider, tid=turn_id, txt=content,
        ))
    print("")

    print("--- Providers Used ---")
    print("  {ids}".format(ids=", ".join(providers_used) if providers_used else "(none)"))
    print("")

    print("--- Turns ({n}) ---".format(n=len(turns)))
    for turn in turns:
        tid = turn.get("turn_id", "?")
        agent = turn.get("agent_id", "?")
        prov = turn.get("provider_id", "?")
        st = turn.get("status", "?")
        turn_attempts = turn.get("attempts", [])
        dur = turn.get("duration_ms")
        dur_str = "{d}ms".format(d=dur) if dur is not None else "?"
        print("  {tid}: {agent} via {prov} -> {st} (attempts={att}, {dur})".format(
            tid=tid, agent=agent, prov=prov, st=st, att=len(turn_attempts), dur=dur_str,
        ))
        for att in turn_attempts:
            att_st = att.get("status", "?")
            att_prov = att.get("provider_id", "?")
            att_dur = att.get("duration_ms")
            att_summary = att.get("summary") or att.get("error") or ""
            is_notable = att_st in ("blocked", "failed")
            has_meaningful_summary = att_summary and att_summary != "completed"
            if is_notable or has_meaningful_summary:
                dur_s = "{d}ms".format(d=att_dur) if att_dur is not None else "?"
                snippet = att_summary[:120] if att_summary else ""
                print("    attempt {n}: [{prov}] {st} ({dur}) {s}".format(
                    n=att.get("attempt", "?"), prov=att_prov, st=att_st,
                    dur=dur_s, s=snippet,
                ))
    print("")

    print("--- Artifacts ({n}) ---".format(n=len(artifacts)))
    for art in artifacts:
        owner = art.get("agent_id") or "filesystem"
        print("  {path} (owner={owner})".format(path=art["path"], owner=owner))
    print("")

    print("--- Tool Events: {n} ---".format(n=len(tool_events)))
    print("--- Interrupts:  {n} ---".format(n=len(interrupts)))
    print("=" * 60)

    summary = {
        "mode": mode,
        "mission_id": mission["id"],
        "final_status": mission["status"],
        "agent_message_count": len(agent_msgs),
        "provider_ids": providers_used,
        "artifact_paths": [a["path"] for a in artifacts],
        "turn_statuses": [t["status"] for t in turns],
        "tool_event_count": len(tool_events),
        "interrupt_count": len(interrupts),
        "temporary_workspace": workspace,
        "cleanup": "automatic",
    }
    print("\nJSON Summary:")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def run_smoke(mode: str, goal: str, max_turns: int, timeout: int) -> bool:
    with tempfile.TemporaryDirectory(prefix="yiren_smoke_") as tmp:
        tmp_dir = Path(tmp)
        print("Temporary workspace: {ws}".format(ws=tmp))
        settings = build_settings(mode, tmp_dir, max_turns, cli_timeout=timeout)
        app = create_app(settings)
        client = TestClient(app)

        print("Creating mission ({mode} mode)...".format(mode=mode))
        t0 = time.monotonic()
        resp = client.post("/api/missions", json={"goal": goal})
        if resp.status_code != 201:
            print("ERROR: create mission returned {code}".format(code=resp.status_code))
            print(resp.text)
            return False

        mission_id = resp.json()["mission"]["id"]
        print("Mission: {mid}".format(mid=mission_id))
        print("Waiting for terminal status (timeout={t}s)...".format(t=timeout))

        detail = wait_terminal(client, mission_id, timeout)
        elapsed = time.monotonic() - t0
        print_report(detail, mode, elapsed, workspace=tmp)

        status = detail["mission"]["status"]
        if status == "completed":
            print("\nPASS: mission completed successfully.")
            return True
        else:
            print("\nWARN: mission ended with status '{st}'.".format(st=status))
            if mode == "real":
                print("  (Real CLI may fail due to auth/quota — check provider config)")
            return status == "completed"


def main():
    parser = argparse.ArgumentParser(
        description="YiRen Hub smoke acceptance test",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fake", action="store_true", help="Run with fake CLI adapter")
    group.add_argument("--real", action="store_true", help="Run with real CLI providers (Codex/Antigravity)")
    parser.add_argument("--goal", default=DEFAULT_GOAL, help="Mission goal text")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="Max turns")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT, help="Timeout in seconds")
    args = parser.parse_args()

    mode = "fake" if args.fake else "real"
    success = run_smoke(mode, args.goal, args.max_turns, args.timeout_seconds)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
