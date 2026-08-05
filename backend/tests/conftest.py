from pathlib import Path

import pytest

from backend.app.settings import Settings


@pytest.fixture()
def temp_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "yirenhub.sqlite3",
        artifact_root=tmp_path / "artifacts",
        config_dir=Path("config"),
        use_fake_cli=True,
        max_turns=6,
        max_a2a_invocations=8,
    )


@pytest.fixture()
def repository(temp_settings: Settings):
    from backend.app.storage.repository import Repository

    repo = Repository(temp_settings.database_path)
    repo.initialize()
    return repo
