import json
import stat
import pytest

from backend.tests.test_hub_settings_api import _make_client
from backend.app.storage.api_connections import credential_dir
from backend.app.storage.hub_settings import default_path, load


@pytest.mark.parametrize("failure", ["metadata", "second_key", "fsync"])
def test_failed_save_removes_only_staged_credentials(tmp_path, monkeypatch, failure):
    from backend.app.storage import api_connections, hub_settings

    client, settings = _make_client(tmp_path)
    assert client.put("/api/hub/settings", json={"connections": {
        "api-test": connection(key_action="replace", api_key="original-secret")
    }}).status_code == 200
    before = load(default_path(settings))
    original_files = set(credential_dir(settings).iterdir())

    def fail(*args, **kwargs):
        raise OSError("failure containing sensitive upstream details")

    if failure == "metadata":
        monkeypatch.setattr(hub_settings, "save", fail)
    elif failure == "fsync":
        monkeypatch.setattr(api_connections.os, "fsync", fail)
    else:
        original_write = api_connections.write_key
        calls = []

        def fail_second(*args):
            calls.append(True)
            return original_write(*args) if len(calls) == 1 else fail()

        monkeypatch.setattr(api_connections, "write_key", fail_second)
    response = client.put("/api/hub/settings", json={"connections": {
        "api-test": {"key_action": "replace", "api_key": "replacement-secret"},
        "api-other": connection(key_action="replace", api_key="other-secret"),
    }})
    assert response.status_code == 500
    assert "sensitive upstream" not in response.text
    assert load(default_path(settings)) == before
    assert set(credential_dir(settings).iterdir()) == original_files
    assert api_connections.read_key(settings, before["connections"]["api-test"]) == "original-secret"


def connection(**updates):
    return dict(name="Private account", protocol="openai_responses",
                base_url="https://example.com/v1", enabled=True, timeout=60,
                allow_private=False, **updates)


def test_credentials_are_not_returned_or_stored_in_settings(tmp_path):
    client, settings = _make_client(tmp_path)
    patch = connection(key_action="replace", api_key="secret-test-value")
    response = client.put("/api/hub/settings", json={"connections": {"api-test": patch}})
    assert response.status_code == 200
    assert "secret-test-value" not in response.text
    assert "key_ref" not in response.text
    stored = load(default_path(settings))
    assert "secret-test-value" not in json.dumps(stored)
    assert response.json()["connections"][-1]["has_key"] is True
    secret = credential_dir(settings) / stored["connections"]["api-test"]["key_ref"]
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert stat.S_IMODE(secret.parent.stat().st_mode) == 0o700
    response = client.put("/api/hub/settings", json={"connections": {"api-test": {"key_action": "delete"}}})
    assert response.status_code == 200
    assert response.json()["connections"][-1]["has_key"] is False
    assert not secret.exists()


def test_endpoint_change_requires_credential_action(tmp_path):
    client, settings = _make_client(tmp_path)
    assert client.put("/api/hub/settings", json={"connections": {"api-test": connection(key_action="replace", api_key="private")}}).status_code == 200
    before = load(default_path(settings))
    response = client.put("/api/hub/settings", json={"connections": {"api-test": {"base_url": "https://other.example/v1"}}, "runtime": {"max_turns": 9}})
    assert response.status_code == 400
    assert load(default_path(settings)) == before


def test_rejects_unsafe_urls_without_committing(tmp_path):
    client, settings = _make_client(tmp_path)
    for url in ["http://example.com", "https://user:password@example.com", "https://127.0.0.1", "https://[::1]", "https://example.com?key=secret"]:
        patch = connection()
        patch["base_url"] = url
        response = client.put("/api/hub/settings", json={"connections": {"api-test": patch}})
        assert response.status_code == 400
        assert "connections" not in load(default_path(settings))


def test_validation_response_does_not_echo_nested_secrets(tmp_path):
    client, _ = _make_client(tmp_path)
    response = client.put("/api/hub/settings", json={"connections": [{"api_key": "do-not-echo"}]})
    assert response.status_code == 422
    assert "do-not-echo" not in response.text


def test_explicit_private_opt_in_and_models_required(tmp_path):
    client, _ = _make_client(tmp_path)
    patch = connection()
    patch.update(base_url="http://127.0.0.1:8080/v1", allow_private=True)
    assert client.put("/api/hub/settings", json={"connections": {"api-local": patch}}).status_code == 200
    response = client.put("/api/hub/settings", json={"agents": {"baobao": {"default_provider_id": "api-local"}}})
    assert response.status_code == 400
    response = client.put("/api/hub/settings", json={"agents": {"baobao": {"default_provider_id": "api-local", "provider_models": {"api-local": "model-from-account"}}}})
    assert response.status_code == 200
    assert client.put("/api/hub/settings", json={"connections": {"api-local": {"delete": True}}}).status_code == 400
