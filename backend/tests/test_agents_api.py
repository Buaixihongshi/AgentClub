from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_get_agents_returns_canonical_three(temp_settings):
    client = TestClient(create_app(temp_settings))
    response = client.get("/api/agents")
    assert response.status_code == 200
    body = response.json()
    ids = [a["id"] for a in body["agents"]]
    assert ids == ["chulan", "baobao", "wangye"]


def test_get_agents_includes_aliases_and_display_names(temp_settings):
    client = TestClient(create_app(temp_settings))
    body = client.get("/api/agents").json()
    wangye = next(a for a in body["agents"] if a["id"] == "wangye")
    assert wangye["display_name"] == "王也"
    assert "王也" in wangye["aliases"]
    assert "wangye" in wangye["aliases"]
    assert "老王" in wangye["aliases"]
