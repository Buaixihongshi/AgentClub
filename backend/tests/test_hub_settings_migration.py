from backend.app.storage.hub_settings import load, retire_provider, save


def test_retire_provider_removes_overrides_and_rewrites_routes(tmp_path):
    path = tmp_path / "hub_settings.json"
    save(
        path,
        {
            "schema_version": 1,
            "providers": {
                "gemini": {"enabled": True},
                "antigravity": {"enabled": False},
            },
            "agents": {
                "chulan": {
                    "default_provider_id": "gemini",
                    "fallback_provider_ids": ["codex", "gemini", "antigravity"],
                },
                "baobao": {
                    "default_provider_id": "codex",
                    "fallback_provider_ids": ["gemini"],
                },
            },
            "onboarding": {
                "verified_providers": {"gemini": {"verified_at": "now"}},
                "verification_provider_id": "gemini",
                "verification_mission_id": "m_old",
                "activated_provider_id": "gemini",
            },
        },
    )

    changed = retire_provider(path, "gemini", "antigravity")
    data = load(path)

    assert changed is True
    assert "gemini" not in data["providers"]
    assert data["agents"]["chulan"] == {
        "default_provider_id": "antigravity",
        "fallback_provider_ids": ["codex"],
    }
    assert data["agents"]["baobao"]["fallback_provider_ids"] == ["antigravity"]
    assert data["onboarding"]["verified_providers"] == {}
    assert "verification_provider_id" not in data["onboarding"]
    assert "activated_provider_id" not in data["onboarding"]


def test_retire_provider_is_idempotent(tmp_path):
    path = tmp_path / "hub_settings.json"
    save(path, {"schema_version": 1, "providers": {"codex": {"enabled": True}}})

    assert retire_provider(path, "gemini", "antigravity") is False
