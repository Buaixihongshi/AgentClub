from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_backend_serves_prebuilt_frontend_without_shadowing_api(temp_settings, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Unity</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('unity')", encoding="utf-8")
    client = TestClient(create_app(temp_settings.model_copy(update={"frontend_dist": dist})))

    index = client.get("/")
    assert index.status_code == 200 and "<title>Unity</title>" in index.text
    assert client.get("/assets/app.js").text == "console.log('unity')"
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/does-not-exist").status_code == 404


def test_frontend_is_not_served_by_default(temp_settings):
    client = TestClient(create_app(temp_settings))
    assert client.get("/").status_code == 404
