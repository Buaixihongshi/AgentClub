#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.main import create_app
from backend.app.settings import Settings
from fastapi.testclient import TestClient

def run_test():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        settings = Settings(
            database_path=tmp_dir / "yirenhub.sqlite3",
            artifact_root=tmp_dir / "artifacts",
            config_dir=Path("config"),
            use_fake_cli=True,
            max_turns=1,
        )
        app = create_app(settings)
        client = TestClient(app)

        resp = client.post("/api/missions", json={"goal": "Test log_thought"})
        mission_id = resp.json()["mission"]["id"]

        # Wait for completion (fake is instant)
        import time
        for _ in range(10):
            detail = client.get(f"/api/missions/{mission_id}").json()
            if detail["mission"]["status"] in ("completed", "failed", "blocked"):
                break
            time.sleep(0.1)

        print(f"Mission Status: {detail['mission']['status']}")
        thoughts = detail.get("thoughts", [])
        print(f"Thoughts count: {len(thoughts)}")
        for t in thoughts:
            kind = t.get("kind", "reasoning")
            content = t.get("content", "")
            agent = t.get("agent_id", "unknown")
            tags = t.get("tags", [])
            print(f"  [{agent}] {kind}: {content} (tags: {tags})")

        if len(thoughts) > 0:
            print("PASS: log_thought events found!")
            return True
        else:
            print("FAIL: No log_thought events found.")
            return False

if __name__ == "__main__":
    sys.exit(0 if run_test() else 1)
