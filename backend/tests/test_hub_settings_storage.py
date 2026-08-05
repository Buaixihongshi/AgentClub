"""Tests for hub_settings file IO layer."""

import json

import pytest

from backend.app.storage.hub_settings import HubSettingsError, default_path, load, save


def test_load_returns_empty_when_missing(tmp_path):
    assert load(tmp_path / "nope.json") == {}


def test_save_then_load_roundtrips(tmp_path):
    p = tmp_path / "hub_settings.json"
    data = {"schema_version": 1, "providers": {"claude": {"enabled": True}}}
    save(p, data)
    assert load(p) == data


def test_load_raises_on_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not-json", encoding="utf-8")
    with pytest.raises(HubSettingsError):
        load(p)


def test_save_writes_atomically(tmp_path):
    p = tmp_path / "hub_settings.json"
    original = {"schema_version": 1, "runtime": {"max_turns": 6}}
    save(p, original)

    # Simulate a crash during write by making the tmp file dir read-only after
    # first save. We test that the original file is preserved when save fails.
    import unittest.mock as mock

    with mock.patch("backend.app.storage.hub_settings.json.dumps", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            save(p, {"schema_version": 1, "runtime": {"max_turns": 99}})

    assert load(p) == original


def test_save_creates_parent_dir_if_missing(tmp_path):
    p = tmp_path / "deep" / "nested" / "hub_settings.json"
    data = {"schema_version": 1, "runtime": {"max_turns": 6}}
    save(p, data)
    assert p.is_file()
    assert load(p) == data


def test_default_path_uses_data_dir():
    from backend.app.settings import Settings
    from pathlib import Path

    s = Settings(database_path=Path("/tmp/mydata/yirenhub.sqlite3"))
    result = default_path(s)
    assert result == Path("/tmp/mydata/hub_settings.json")
