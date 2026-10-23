import threading
import time

from fastapi.testclient import TestClient

from backend.app.domain.models import Mission, MissionStatus
from backend.app.main import create_app
from backend.app.runtime_control import activity
from backend.app.settings import get_settings, snapshot_settings
from backend.app.storage import hub_settings


def client_for(settings, monkeypatch):
    monkeypatch.setenv("YIREN_LIFECYCLE_TOKEN", "test-private-control")
    app = create_app(settings)
    return TestClient(app), app


def test_drain_requires_private_token_and_blocks_writes_only(temp_settings, monkeypatch):
    client, app = client_for(temp_settings, monkeypatch)
    assert client.post("/api/runtime/drain").status_code == 403
    assert client.post("/api/runtime/drain", headers={"X-AgentClub-Control": "wrong"}).status_code == 403
    headers = {"X-AgentClub-Control": "test-private-control"}
    assert client.post("/api/runtime/drain", headers=headers).status_code == 200
    assert client.post("/api/conversations", json={"title": "blocked"}).status_code == 503
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["runtime"]["draining"] is True
    assert "test-private-control" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/api/runtime/resume", headers=headers).status_code == 200
    assert client.post("/api/conversations", json={"title": "allowed"}).status_code == 201


def test_busy_drain_does_not_disable_new_requests(temp_settings, monkeypatch):
    client, app = client_for(temp_settings, monkeypatch)
    from backend.app.storage.repository import Repository
    repo = Repository(temp_settings.database_path)
    repo.create_mission(Mission(id="busy", title="test", goal="unfinished", status=MissionStatus.AWAITING_INPUT))
    result = client.post("/api/runtime/drain", headers={"X-AgentClub-Control": "test-private-control"})
    assert result.status_code == 409
    assert client.get("/api/health").json()["runtime"]["draining"] is False
    assert activity(repo)["active_missions"] == 1


def test_drain_waits_for_an_inflight_mutation_and_sees_its_work(temp_settings, monkeypatch):
    client, app = client_for(temp_settings, monkeypatch)
    from backend.app.storage.repository import Repository
    repo = Repository(temp_settings.database_path)
    entered = threading.Event()
    release = threading.Event()

    @app.post("/test/admission")
    def admission():
        entered.set()
        assert release.wait(5)
        repo.create_mission(Mission(id="admitted", title="test", goal="unfinished"))
        return {"ok": True}

    results = []
    with client:
        writer = threading.Thread(target=lambda: results.append(client.post("/test/admission")))
        writer.start()
        assert entered.wait(5)
        drainer = threading.Thread(target=lambda: results.append(client.post("/api/runtime/drain", headers={"X-AgentClub-Control": "test-private-control"})))
        drainer.start()
        release.set()
        writer.join(5)
        drainer.join(5)
    assert sorted(r.status_code for r in results) == [200, 409]
    assert not app.state.draining


def test_demo_only_survives_saved_runtime_overrides_and_blocks_real_verification(temp_settings, monkeypatch):
    monkeypatch.setenv("YIREN_DATABASE_PATH", str(temp_settings.database_path))
    monkeypatch.setenv("YIREN_DEMO_ONLY", "true")
    hub_settings.save(hub_settings.default_path(temp_settings), {"runtime": {"use_fake_cli": False, "maintenance_enabled": True}})
    settings = get_settings()
    assert settings.demo_only and settings.use_fake_cli and not settings.maintenance_enabled
    snap = snapshot_settings(settings)
    assert snap.demo_only and snap.use_fake_cli and not snap.maintenance_enabled
    client = TestClient(create_app(settings))
    assert client.post("/api/onboarding/providers/codex/verify").status_code == 409
    assert client.post("/api/providers/codex/probe").status_code == 409
