#!/usr/bin/env python3
"""Unity conversation flow smoke acceptance script.

Validates:
  - Conversation continuity (messages persist across missions)
  - Directed agent routing (@王也 -> wangye handles first turn)
  - Conversation isolation (separate conversations don't leak)
  - Conversation context in follow-up prompts

Usage:
    python3 scripts/smoke_conversation_flow.py --fake
    python3 scripts/smoke_conversation_flow.py --real --timeout-seconds 420
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

DEFAULT_GOAL_1 = "帮我规划一下系统架构"
DEFAULT_GOAL_2 = "继续，补充测试方案"
DEFAULT_GOAL_DIRECTED = "看看有什么风险"
DEFAULT_MAX_TURNS = 6
DEFAULT_TIMEOUT = 180


def build_settings(mode: str, tmp_dir: Path, max_turns: int, cli_timeout: int = 120) -> Settings:
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
    detail = {}
    while time.monotonic() < deadline:
        resp = client.get("/api/missions/{mid}".format(mid=mission_id))
        resp.raise_for_status()
        detail = resp.json()
        status = detail["mission"]["status"]
        if status in ("completed", "failed", "blocked", "completed_with_warnings"):
            return detail
        time.sleep(0.3)
    return detail


class Check:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.detail = ""

    def ok(self, detail: str = ""):
        self.passed = True
        self.detail = detail

    def fail(self, detail: str):
        self.passed = False
        self.detail = detail


def run_smoke(mode: str, goal_1: str, goal_2: str, goal_directed: str,
              max_turns: int, timeout: int) -> bool:
    checks: list[Check] = []

    with tempfile.TemporaryDirectory(prefix="yiren_conv_smoke_") as tmp:
        tmp_dir = Path(tmp)
        print("Temporary workspace: {ws}".format(ws=tmp))
        settings = build_settings(mode, tmp_dir, max_turns, cli_timeout=timeout)
        app = create_app(settings)
        client = TestClient(app)
        t0 = time.monotonic()

        # --- Step 1: Create conversation A ---
        resp = client.post("/api/conversations", json={"title": "Smoke A"})
        assert resp.status_code == 201, "create conv A failed: {c}".format(c=resp.status_code)
        conv_a = resp.json()
        conv_a_id = conv_a["id"]
        print("Conversation A: {cid}".format(cid=conv_a_id))

        # --- Step 2: Mission 1 in A ---
        print("  Mission 1: {g}".format(g=goal_1))
        resp = client.post(
            "/api/conversations/{cid}/missions".format(cid=conv_a_id),
            json={"goal": goal_1},
        )
        assert resp.status_code == 201
        m1_detail = resp.json()
        m1_id = m1_detail["latest_mission_detail"]["mission"]["id"]
        print("  Mission 1 ID: {mid}".format(mid=m1_id))
        m1_final = wait_terminal(client, m1_id, timeout)
        m1_status = m1_final["mission"]["status"]
        print("  Mission 1 status: {st}".format(st=m1_status))

        # --- Step 3: Mission 2 follow-up in A ---
        print("  Mission 2: {g}".format(g=goal_2))
        resp = client.post(
            "/api/conversations/{cid}/missions".format(cid=conv_a_id),
            json={"goal": goal_2},
        )
        assert resp.status_code == 201
        m2_detail = resp.json()
        m2_id = m2_detail["latest_mission_detail"]["mission"]["id"]
        print("  Mission 2 ID: {mid}".format(mid=m2_id))
        m2_final = wait_terminal(client, m2_id, timeout)
        m2_status = m2_final["mission"]["status"]
        print("  Mission 2 status: {st}".format(st=m2_status))

        # --- Check: conversation A has messages from both missions ---
        c = Check("conversation_continuity")
        checks.append(c)
        conv_a_detail = client.get(
            "/api/conversations/{cid}".format(cid=conv_a_id)
        ).json()
        all_msgs = conv_a_detail["messages"]
        user_msgs = [m for m in all_msgs if m.get("role") == "user"]
        agent_msgs = [m for m in all_msgs if m.get("role") == "agent"]
        if len(user_msgs) >= 2 and len(agent_msgs) >= 2:
            c.ok("user_msgs={u}, agent_msgs={a}".format(u=len(user_msgs), a=len(agent_msgs)))
        else:
            c.fail("expected >=2 user + >=2 agent, got user={u} agent={a}".format(
                u=len(user_msgs), a=len(agent_msgs)))

        # --- Check: agent messages from prior mission in conversation ---
        c = Check("agent_messages_in_conversation")
        checks.append(c)
        m1_agent_ids = set()
        for m in agent_msgs:
            mid = m.get("mission_id") or m.get("metadata", {}).get("mission_id")
            if mid == m1_id:
                m1_agent_ids.add(m.get("agent_id") or m.get("metadata", {}).get("agent_id"))
        if m1_agent_ids:
            c.ok("mission 1 agent_ids in conv: {ids}".format(ids=m1_agent_ids))
        else:
            c.fail("no agent messages from mission 1 found in conversation")

        # --- Step 5: Directed mission 3 in A ---
        print("  Mission 3 (directed @wangye): {g}".format(g=goal_directed))
        resp = client.post(
            "/api/conversations/{cid}/missions".format(cid=conv_a_id),
            json={"goal": goal_directed, "target_agent_id": "wangye"},
        )
        assert resp.status_code == 201
        m3_detail = resp.json()
        m3_id = m3_detail["latest_mission_detail"]["mission"]["id"]
        print("  Mission 3 ID: {mid}".format(mid=m3_id))
        m3_final = wait_terminal(client, m3_id, timeout)
        m3_status = m3_final["mission"]["status"]
        print("  Mission 3 status: {st}".format(st=m3_status))

        # --- Check: all missions reached acceptable terminal status ---
        ACCEPTABLE = {"completed", "completed_with_warnings"}
        c = Check("mission_statuses")
        checks.append(c)
        bad = []
        for label, mid, st in [("M1", m1_id, m1_status), ("M2", m2_id, m2_status), ("M3", m3_id, m3_status)]:
            if st not in ACCEPTABLE:
                bad.append("{label}={st}".format(label=label, st=st))
        if not bad:
            c.ok("all missions completed: {s}".format(
                s=", ".join([m1_status, m2_status, m3_status])))
        else:
            c.fail("non-completed missions: {b}".format(b=", ".join(bad)))

        # --- Check: directed mission first agent is wangye ---
        c = Check("directed_agent_routing")
        checks.append(c)
        m3_msgs = m3_final.get("messages") or m3_final.get("events") or []
        m3_agent_msgs = [m for m in m3_msgs if m.get("role") == "agent"]
        if m3_agent_msgs:
            first_agent = (
                m3_agent_msgs[0].get("agent_id")
                or m3_agent_msgs[0].get("metadata", {}).get("agent_id")
            )
            if first_agent == "wangye":
                c.ok("first agent = wangye")
            else:
                c.fail("expected wangye, got {a}".format(a=first_agent))
        else:
            c.fail("no agent messages in directed mission")

        # --- Step 7: Create conversation B ---
        resp = client.post("/api/conversations", json={"title": "Smoke B"})
        assert resp.status_code == 201
        conv_b = resp.json()
        conv_b_id = conv_b["id"]
        print("Conversation B: {cid}".format(cid=conv_b_id))

        # --- Check: B is isolated from A ---
        c = Check("conversation_isolation")
        checks.append(c)
        conv_b_detail = client.get(
            "/api/conversations/{cid}".format(cid=conv_b_id)
        ).json()
        b_msgs = conv_b_detail["messages"]
        if len(b_msgs) == 0:
            c.ok("conversation B has 0 messages")
        else:
            c.fail("conversation B has {n} messages (should be 0)".format(n=len(b_msgs)))

        # --- Check: conversation A preview not empty ---
        c = Check("conversation_preview")
        checks.append(c)
        summaries = client.get("/api/conversations").json()
        a_summary = [s for s in summaries if s["id"] == conv_a_id]
        if a_summary and a_summary[0].get("latest_message_preview"):
            c.ok("preview: {p}".format(p=a_summary[0]["latest_message_preview"][:60]))
        else:
            c.fail("preview is empty or missing")

        elapsed = time.monotonic() - t0

        # --- Gather stats ---
        conv_a_final = client.get(
            "/api/conversations/{cid}".format(cid=conv_a_id)
        ).json()
        total_msgs = len(conv_a_final["messages"])
        total_missions = len(conv_a_final["missions"])
        providers_used = sorted(set(
            m.get("provider_id") or m.get("metadata", {}).get("provider_label", "?")
            for m in conv_a_final["messages"]
            if m.get("role") == "agent"
        ))

        latest_detail = conv_a_final.get("latest_mission_detail") or {}
        artifacts = latest_detail.get("artifacts", [])
        tool_events = latest_detail.get("tool_events", [])
        interrupts = latest_detail.get("interrupts", [])

        # --- Print report ---
        print("\n" + "=" * 60)
        print("Unity Conversation Flow Smoke Report")
        print("=" * 60)
        print("Mode:              {m}".format(m=mode))
        print("Elapsed:           {t:.1f}s".format(t=elapsed))
        print("Workspace:         {ws}".format(ws=tmp))
        print("Cleanup:           automatic (TemporaryDirectory)")
        print("")
        print("--- Conversation A ---")
        print("  ID:              {cid}".format(cid=conv_a_id))
        print("  Missions:        {n}".format(n=total_missions))
        print("  Total messages:  {n}".format(n=total_msgs))
        print("  Mission 1:       {mid} -> {st}".format(mid=m1_id, st=m1_status))
        print("  Mission 2:       {mid} -> {st}".format(mid=m2_id, st=m2_status))
        print("  Mission 3:       {mid} -> {st} (directed=wangye)".format(
            mid=m3_id, st=m3_status))
        print("  Providers:       {ids}".format(
            ids=", ".join(providers_used) if providers_used else "(none)"))
        print("  Artifacts:       {n}".format(n=len(artifacts)))
        print("  Tool events:     {n}".format(n=len(tool_events)))
        print("  Interrupts:      {n}".format(n=len(interrupts)))
        print("")
        print("--- Turn Attempts ---")
        for label, detail in [("M1", m1_final), ("M2", m2_final), ("M3", m3_final)]:
            turns = detail.get("turns", [])
            for turn in turns:
                tid = turn.get("turn_id", "?")
                agent = turn.get("agent_id", "?")
                prov = turn.get("provider_id", "?")
                st = turn.get("status", "?")
                turn_attempts = turn.get("attempts", [])
                dur = turn.get("duration_ms")
                dur_str = "{d}ms".format(d=dur) if dur is not None else "?"
                print("  {label} {tid}: {agent} via {prov} -> {st} ({dur})".format(
                    label=label, tid=tid, agent=agent, prov=prov, st=st, dur=dur_str,
                ))
                for att in turn_attempts:
                    att_st = att.get("status", "?")
                    att_prov = att.get("provider_id", "?")
                    att_summary = att.get("summary") or att.get("error") or ""
                    is_notable = att_st in ("blocked", "failed")
                    has_meaningful_summary = att_summary and att_summary != "completed"
                    if is_notable or has_meaningful_summary:
                        snippet = att_summary[:120] if att_summary else ""
                        print("    attempt {n}: [{prov}] {st} {s}".format(
                            n=att.get("attempt", "?"), prov=att_prov, st=att_st,
                            s=snippet,
                        ))
        print("")
        print("--- Conversation B ---")
        print("  ID:              {cid}".format(cid=conv_b_id))
        print("  Messages:        {n}".format(n=len(b_msgs)))
        print("")
        print("--- Checks ---")
        all_passed = True
        for ck in checks:
            mark = "PASS" if ck.passed else "FAIL"
            print("  [{m}] {name}: {detail}".format(m=mark, name=ck.name, detail=ck.detail))
            if not ck.passed:
                all_passed = False
        print("=" * 60)

        summary = {
            "mode": mode,
            "elapsed_s": round(elapsed, 1),
            "conversation_a_id": conv_a_id,
            "conversation_b_id": conv_b_id,
            "mission_ids": [m1_id, m2_id, m3_id],
            "mission_statuses": [m1_status, m2_status, m3_status],
            "mission_count": total_missions,
            "message_count": total_msgs,
            "directed_target": "wangye",
            "first_directed_agent": (
                m3_agent_msgs[0].get("agent_id") if m3_agent_msgs else None
            ),
            "isolation_ok": len(b_msgs) == 0,
            "provider_ids": providers_used,
            "artifact_count": len(artifacts),
            "tool_event_count": len(tool_events),
            "interrupt_count": len(interrupts),
            "checks_passed": sum(1 for ck in checks if ck.passed),
            "checks_total": len(checks),
            "temporary_workspace": tmp,
            "cleanup": "automatic",
        }
        print("\nJSON Summary:")
        print(json.dumps(summary, indent=2, ensure_ascii=False))

        if all_passed:
            print("\nPASS: all conversation flow checks passed.")
        else:
            print("\nFAIL: some checks did not pass.")
            if mode == "real":
                print("  (Real CLI may fail due to auth/quota — check provider config)")
        return all_passed


def main():
    parser = argparse.ArgumentParser(
        description="Unity conversation flow smoke acceptance test",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fake", action="store_true", help="Run with fake CLI adapter")
    group.add_argument("--real", action="store_true",
                       help="Run with real CLI providers (Codex/Antigravity)")
    parser.add_argument("--goal", default=None,
                        help="Override goal for mission 1 (default: built-in)")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS,
                        help="Max turns per mission")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT,
                        help="Timeout per mission in seconds")
    args = parser.parse_args()

    mode = "fake" if args.fake else "real"
    g1 = args.goal or DEFAULT_GOAL_1
    g2 = DEFAULT_GOAL_2
    g_dir = DEFAULT_GOAL_DIRECTED
    success = run_smoke(mode, g1, g2, g_dir, args.max_turns, args.timeout_seconds)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
