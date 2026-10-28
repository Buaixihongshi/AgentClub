#!/usr/bin/env python3
"""Fresh-install onboarding acceptance smoke.

Onboarding always verifies a real CLI. ``--fake`` keeps this smoke offline:
a scripted test double stands in for the installed CLI, so no model is called.
Real-CLI acceptance goes through a managed development environment instead.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from fastapi.testclient import TestClient
except ImportError:
    sys.exit("httpx / fastapi[testclient] not installed. Run: pip install httpx")

from backend.app.adapters.base import CLIAdapter, TurnOutcome
from backend.app.api import onboarding as onboarding_api
from backend.app.domain.models import TurnStatus
from backend.app.main import create_app
from backend.app.settings import Settings


class ScriptedCLI(CLIAdapter):
    """Offline stand-in for an installed CLI that replies through MCP."""

    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        tools.publish_message(
            mission_id,
            agent_id,
            turn_id,
            "{agent} 已通过 {provider} 回复".format(agent=agent_id, provider=provider_id),
            provider_id,
        )
        return TurnOutcome(status=TurnStatus.COMPLETED)


def wait_for(client: TestClient, expected: str, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = client.get("/api/onboarding").json()
        if state["state"] == expected:
            return state
        time.sleep(0.05)
    raise AssertionError("onboarding did not reach {state}".format(state=expected))


def run_offline_smoke() -> None:
    installed = {"codex"}
    onboarding_api._provider_readiness = lambda settings, provider_id: {
        "available": provider_id in installed,
        "version": "{id} smoke".format(id=provider_id) if provider_id in installed else None,
        "path": None,
        "error": None if provider_id in installed else "{id} 未安装".format(id=provider_id),
    }
    with tempfile.TemporaryDirectory(prefix="yiren-onboarding-") as tmp:
        root = Path(tmp)
        settings = Settings(
            database_path=root / "data" / "yirenhub.sqlite3",
            artifact_root=root / "artifacts",
            config_dir=PROJECT_ROOT / "config",
            use_fake_cli=True,
            max_turns=6,
            max_a2a_invocations=8,
        )
        app = create_app(settings, onboarding_verification_adapter=ScriptedCLI())
        with TestClient(app) as client:
            health = client.get("/api/health")
            assert health.status_code == 200, health.text

            initial = client.get("/api/onboarding").json()
            assert initial["state"] == "ready_for_first_chat", initial
            assert initial["recommended_provider_id"] == "codex", initial
            missing = [item["id"] for item in initial["providers"] if not item["available"]]
            assert missing == ["claude", "antigravity"], initial
            assert all(item["install_command"] for item in initial["providers"]), initial

            payload = {"provider_id": "codex", "confirmed_real_call": True}
            first = client.post("/api/onboarding/first-chat", json=payload)
            second = client.post("/api/onboarding/first-chat", json=payload)
            assert first.status_code == 202, first.text
            assert second.status_code == 202, second.text
            first_mission = first.json()["conversation"]["latest_mission_detail"]["mission"]["id"]
            second_mission = second.json()["conversation"]["latest_mission_detail"]["mission"]["id"]
            assert first_mission == second_mission

            state = wait_for(client, "first_chat_succeeded")
            assert state["verified_provider_ids"] == ["codex"], state
            client.post("/api/onboarding/providers/codex/activate").raise_for_status()
            state = wait_for(client, "team_intro_completed")
            detail = client.get(
                "/api/conversations/{id}".format(id=state["conversation_id"])
            ).json()
            replies = [message for message in detail["messages"] if message["role"] == "agent"]
            assert [m["agent_id"] for m in replies] == ["chulan", "baobao", "wangye"], replies
            assert {m["provider_id"] for m in replies} == {"codex"}, replies

            completed = client.patch("/api/onboarding", json={"completed": True})
            assert completed.status_code == 200, completed.text
            assert completed.json()["state"] == "completed"

            print("PASS: detect CLI → idempotent first chat via codex → team intro → completed")
            print("conversation_id={id}".format(id=state["conversation_id"]))
            print("mission_id={id}".format(id=state["mission_id"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Unity onboarding smoke")
    parser.add_argument("--fresh", action="store_true", help="Use an isolated temporary data directory")
    parser.add_argument("--fake", action="store_true", help="Use an offline scripted CLI instead of a real one")
    args = parser.parse_args()
    if not args.fake:
        parser.error("use --fake offline; verify real CLIs in a development environment")
    run_offline_smoke()


if __name__ == "__main__":
    main()
