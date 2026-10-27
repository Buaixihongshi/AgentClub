#!/usr/bin/env python3
"""Smoke test for A2A routing (G3 fire-and-forget + G4 wait_reply).

Default mode uses a custom in-process adapter through the API stack. Optional
real mode can be added later once Codex MCP invocation is stable enough for
quota-dependent manual acceptance.

Usage:
    python3 scripts/smoke_a2a_codex.py --fake --timeout-seconds 120
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from backend.app.adapters.base import CLIAdapter, TurnOutcome
from backend.app.domain.models import TurnStatus
from backend.app.mcp.tools import MCPToolError, MCPTools
from backend.app.settings import Settings


class A2ASmokeAdapter(CLIAdapter):
    """Adapter that makes chulan invoke baobao via A2A, then publishes a message."""

    def run_turn(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        prompt: str,
        tools: MCPTools,
    ) -> TurnOutcome:
        if agent_id == "chulan":
            try:
                tools.invoke_agent(
                    mission_id,
                    agent_id,
                    turn_id,
                    "baobao",
                    "核对异闻物档案",
                    provider_id,
                )
            except MCPToolError:
                pass
        tools.publish_message(
            mission_id,
            agent_id,
            turn_id,
            "[smoke:{agent_id}] 发言完毕".format(agent_id=agent_id),
            provider_id,
        )
        return TurnOutcome(status=TurnStatus.COMPLETED)


class WaitReplySmokeAdapter(CLIAdapter):
    """Adapter: chulan calls baobao with wait_reply; baobao publishes a reply;
    chulan resumes and echoes the injected reply text."""

    def __init__(self) -> None:
        self.resume_prompts: list[str] = []

    def run_turn(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        prompt: str,
        tools: MCPTools,
    ) -> TurnOutcome:
        if agent_id == "chulan" and "接续上一轮" not in prompt:
            try:
                tools.invoke_agent(
                    mission_id, agent_id, turn_id,
                    "baobao", "帮忙核查一下", provider_id,
                    mode="wait_reply",
                )
            except MCPToolError:
                pass
            tools.publish_message(
                mission_id, agent_id, turn_id,
                "[smoke:chulan] 初始发言", provider_id,
            )
        elif agent_id == "chulan":
            self.resume_prompts.append(prompt)
            tools.publish_message(
                mission_id, agent_id, turn_id,
                "[smoke:chulan] 收到回复后恢复", provider_id,
            )
        elif agent_id == "baobao":
            tools.publish_message(
                mission_id, agent_id, turn_id,
                "[smoke:baobao] 核查结果：一切正常", provider_id,
            )
        else:
            tools.publish_message(
                mission_id, agent_id, turn_id,
                f"[smoke:{agent_id}] done", provider_id,
            )
        return TurnOutcome(status=TurnStatus.COMPLETED)


def wait_terminal(client: TestClient, mission_id: str, timeout_seconds: int) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        detail = client.get(f"/api/missions/{mission_id}").json()
        if detail["mission"]["status"] in {"completed", "failed", "blocked"}:
            return detail
        time.sleep(0.05)
    raise RuntimeError("mission did not finish before timeout")


def _build_test_app(settings: Settings, adapter: CLIAdapter):
    from backend.app.storage.repository import Repository
    from backend.app.api.conversations import build_router as _build_conv_router
    from backend.app.api.missions import build_router as _build_missions_router
    from backend.app.api.providers import build_router as _build_prov_router
    from fastapi import FastAPI

    repository = Repository(settings.database_path)
    repository.initialize()
    settings.artifact_root.mkdir(parents=True, exist_ok=True)

    missions_router = _build_missions_router(settings, repository, adapter_override=adapter)
    app = FastAPI(title="Unity A2A Smoke")
    app.include_router(_build_conv_router(settings, repository))
    app.include_router(missions_router)
    app.include_router(_build_prov_router(settings))
    return app


def run_fire_and_forget(timeout_seconds: int) -> int:
    print("\n=== A2A fire-and-forget smoke ===")
    with tempfile.TemporaryDirectory(prefix="yiren-a2a-ff-") as tmp:
        root = Path(tmp)
        settings = Settings(
            database_path=root / "db.sqlite3",
            artifact_root=root / "artifacts",
            config_dir=ROOT / "config",
            use_fake_cli=True,
            max_turns=1,
            max_a2a_invocations=2,
            cli_timeout=timeout_seconds,
        )
        app = _build_test_app(settings, A2ASmokeAdapter())
        client = TestClient(app)
        created = client.post(
            "/api/missions",
            json={
                "title": "A2A smoke",
                "goal": "请张楚岚分析后召唤冯宝宝核对一项资料",
                "target_agent_id": "chulan",
            },
        ).json()
        mission_id = created["mission"]["id"]
        detail = wait_terminal(client, mission_id, timeout_seconds)
        invocations = detail.get("a2a_invocations", [])
        messages = detail.get("messages", [])
        baobao_messages = [
            item for item in messages
            if item.get("role") == "agent" and item.get("agent_id") == "baobao"
        ]
        print("mission:", mission_id, detail["mission"]["status"])
        print("a2a_invocations:", len(invocations))
        print("baobao_messages:", len(baobao_messages))
        if detail["mission"]["status"] != "completed":
            print("FAIL: mission not completed, status =", detail["mission"]["status"])
            return 1
        if not invocations:
            print("FAIL: expected at least one a2a invocation")
            return 1
        if not baobao_messages:
            print("FAIL: expected baobao to speak")
            return 1
        print("PASS: A2A fire-and-forget smoke passed.")
        return 0


def run_wait_reply(timeout_seconds: int) -> int:
    print("\n=== A2A wait_reply smoke ===")
    with tempfile.TemporaryDirectory(prefix="yiren-a2a-wr-") as tmp:
        root = Path(tmp)
        settings = Settings(
            database_path=root / "db.sqlite3",
            artifact_root=root / "artifacts",
            config_dir=ROOT / "config",
            use_fake_cli=True,
            max_turns=2,
            max_a2a_invocations=2,
            cli_timeout=timeout_seconds,
        )
        adapter = WaitReplySmokeAdapter()
        app = _build_test_app(settings, adapter)
        client = TestClient(app)
        created = client.post(
            "/api/missions",
            json={
                "title": "A2A wait_reply smoke",
                "goal": "请张楚岚核查后等冯宝宝回复",
                "target_agent_id": "chulan",
            },
        ).json()
        mission_id = created["mission"]["id"]
        detail = wait_terminal(client, mission_id, timeout_seconds)
        status = detail["mission"]["status"]
        invocations = detail.get("a2a_invocations", [])
        messages = detail.get("messages", [])

        wait_reply_invocations = [i for i in invocations if i.get("mode") == "wait_reply"]
        baobao_msgs = [m for m in messages if m.get("role") == "agent" and m.get("agent_id") == "baobao"]
        chulan_msgs = [m for m in messages if m.get("role") == "agent" and m.get("agent_id") == "chulan"]

        print(f"mission: {mission_id} {status}")
        print(f"a2a_invocations: {len(invocations)} (wait_reply: {len(wait_reply_invocations)})")
        print(f"baobao_messages: {len(baobao_msgs)}")
        print(f"chulan_messages: {len(chulan_msgs)}")

        failures = []
        if status != "completed":
            failures.append(f"mission not completed, status={status}")
        if not wait_reply_invocations:
            failures.append("expected at least one wait_reply invocation")
        if not baobao_msgs:
            failures.append("expected baobao to speak")
        if len(chulan_msgs) < 2:
            failures.append(f"expected chulan to speak at least twice (initial + resume), got {len(chulan_msgs)}")

        if wait_reply_invocations:
            inv = wait_reply_invocations[0]
            print(f"  reply_text: {inv.get('reply_text', '<missing>')}")
            print(f"  reply_failed: {inv.get('reply_failed', '<missing>')}")
            if inv.get("reply_text") is None:
                failures.append("wait_reply invocation has no reply_text")
            if inv.get("reply_failed"):
                failures.append("wait_reply invocation has reply_failed=True")

        if adapter.resume_prompts:
            prompt_snippet = adapter.resume_prompts[0][:200]
            has_reply = "a2a_reply_text" in prompt_snippet or "baobao" in prompt_snippet.lower()
            print(f"  resume_prompt_snippet: {prompt_snippet}...")
        else:
            has_reply = False
            print("  WARNING: no resume prompts captured")

        if failures:
            for f in failures:
                print(f"FAIL: {f}")
            return 1
        print("PASS: A2A wait_reply smoke passed.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fake", action="store_true", help="run fake-mode smoke")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args()
    if not args.fake:
        print("Only --fake is supported in A2A smoke. Real Codex validation is manual.")
        return 2
    rc1 = run_fire_and_forget(args.timeout_seconds)
    rc2 = run_wait_reply(args.timeout_seconds)
    if rc1 != 0 or rc2 != 0:
        print(f"\nSUMMARY: fire_and_forget={'PASS' if rc1==0 else 'FAIL'}, wait_reply={'PASS' if rc2==0 else 'FAIL'}")
        return 1
    print("\nSUMMARY: All A2A smoke tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
