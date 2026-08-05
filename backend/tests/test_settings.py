import pytest

from backend.app.settings import get_settings


@pytest.fixture(autouse=True)
def _isolate_hub_settings(monkeypatch, tmp_path):
    """Each test gets a fresh DB path so get_settings()'s hub_settings.json
    lookup misses the developer's local data/hub_settings.json (which carries
    dogfood overrides like cli_timeout=240 and would poison default-value tests).
    """
    monkeypatch.setenv("YIREN_DATABASE_PATH", str(tmp_path / "runtime.sqlite3"))


def test_get_settings_reads_use_fake_cli_env(monkeypatch):
    monkeypatch.setenv("YIREN_USE_FAKE_CLI", "false")

    settings = get_settings()

    assert settings.use_fake_cli is False


def test_get_settings_defaults_to_fake_cli(monkeypatch):
    monkeypatch.delenv("YIREN_USE_FAKE_CLI", raising=False)

    settings = get_settings()

    assert settings.use_fake_cli is True


def test_get_settings_reads_runtime_paths_and_max_turns(monkeypatch, tmp_path):
    monkeypatch.setenv("YIREN_DATABASE_PATH", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("YIREN_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("YIREN_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YIREN_MAX_TURNS", "1")

    settings = get_settings()

    assert settings.database_path == tmp_path / "runtime.sqlite3"
    assert settings.artifact_root == tmp_path / "artifacts"
    assert settings.config_dir == tmp_path / "config"
    assert settings.max_turns == 1


def test_get_settings_reads_cli_timeout(monkeypatch):
    monkeypatch.setenv("YIREN_CLI_TIMEOUT", "300")
    settings = get_settings()
    assert settings.cli_timeout == 300


def test_get_settings_defaults_cli_timeout(monkeypatch):
    monkeypatch.delenv("YIREN_CLI_TIMEOUT", raising=False)
    settings = get_settings()
    assert settings.cli_timeout == 120


def test_max_a2a_invocations_defaults_to_8(monkeypatch):
    monkeypatch.delenv("YIREN_MAX_A2A_INVOCATIONS", raising=False)

    settings = get_settings()

    assert settings.max_a2a_invocations == 8


def test_max_a2a_invocations_reads_env(monkeypatch):
    monkeypatch.setenv("YIREN_MAX_A2A_INVOCATIONS", "3")

    settings = get_settings()

    assert settings.max_a2a_invocations == 3
