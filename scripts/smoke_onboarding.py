#!/usr/bin/env python3
"""Fresh-install onboarding acceptance smoke.

Phase 1 intentionally supports the offline Fake path only. A later phase adds
``--provider`` verification through the real MCP route.
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

from backend.app.main import create_app
from backend.app.settings import Settings


def wait_for_success(client: TestClient, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = client.get("/api/onboarding").json()
        if state["state"] == "first_chat_succeeded":
            return state
        time.sleep(0.05)
    raise AssertionError("onboarding did not reach first_chat_succeeded")


def run_fake_smoke() -> None:
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
        with TestClient(create_app(settings)) as client:
            health = client.get("/api/health")
            assert health.status_code == 200, health.text
            assert health.json()["fake_provider"] == "ready"

            initial = client.get("/api/onboarding").json()
            assert initial["state"] == "ready_for_first_chat", initial

            first = client.post("/api/onboarding/first-chat")
            second = client.post("/api/onboarding/first-chat")
            assert first.status_code == 202, first.text
            assert second.status_code == 202, second.text
            first_body = first.json()
            second_body = second.json()
            first_mission = first_body["conversation"]["latest_mission_detail"]["mission"]["id"]
            second_mission = second_body["conversation"]["latest_mission_detail"]["mission"]["id"]
            assert first_mission == second_mission

            state = wait_for_success(client)
            detail = client.get(
                "/api/conversations/{id}".format(id=state["conversation_id"])
            ).json()
            replies = [message for message in detail["messages"] if message["role"] == "agent"]
            assert len(replies) == 1, replies
            assert replies[0]["agent_id"] == "chulan", replies[0]
            assert replies[0]["provider_id"] == "fake", replies[0]
            assert "AgentClub 这边已经跑通了" in replies[0]["content"], replies[0]
            assert "聊天室模式" not in replies[0]["content"], replies[0]

            completed = client.patch(
                "/api/onboarding",
                json={"completed": True, "dismissed_real_cli_setup": True},
            )
            assert completed.status_code == 200, completed.text
            assert completed.json()["state"] == "completed"

            print("PASS: health → idempotent first chat → 张楚岚演示回复 → completed")
            print("conversation_id={id}".format(id=state["conversation_id"]))
            print("mission_id={id}".format(id=state["mission_id"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentClub onboarding smoke")
    parser.add_argument("--fresh", action="store_true", help="Use an isolated temporary data directory")
    parser.add_argument("--fake", action="store_true", help="Run the offline Fake onboarding path")
    args = parser.parse_args()
    if not args.fake:
        parser.error("Phase 1 currently requires --fake")
    run_fake_smoke()


if __name__ == "__main__":
    main()
