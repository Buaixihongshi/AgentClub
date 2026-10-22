import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from scripts import checks


def test_failed_check_cannot_produce_passing_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["checks.py", "backend", "--output", str(tmp_path)])
    monkeypatch.setattr(checks, "commands", lambda *a: {"backend": [[sys.executable, "-c", "raise SystemExit(7)"]]})
    assert checks.main() == 1
    report = json.loads((tmp_path / "checks.json").read_text())
    assert report["results"][0]["exit_code"] == 7
    assert report["passed"] is False


def test_source_drift_invalidates_successful_check_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["checks.py", "backend", "--output", str(tmp_path)])
    monkeypatch.setattr(checks, "commands", lambda *a: {"backend": [[sys.executable, "-c", "pass"]]})
    snapshots = iter([{"git_sha": "one", "source_digest": "first"}, {"git_sha": "one", "source_digest": "second"}])
    monkeypatch.setattr(checks, "source_snapshot", lambda: next(snapshots))
    assert checks.main() == 1
    report = json.loads((tmp_path / "checks.json").read_text())
    assert report["source_changed"] and not report["passed"]


def test_git_privacy_scans_candidate_secrets_but_not_ignored_runtime(tmp_path, monkeypatch, capsys):
    source = Path("scripts/privacy-check.py").resolve()
    spec = importlib.util.spec_from_file_location("privacy_check", source)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".yiren/\n")
    runtime = tmp_path / ".yiren"
    runtime.mkdir()
    secret = "sk-" + "A" * 24
    (runtime / "private.txt").write_text(secret)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["privacy-check.py", "--git"])
    assert checker.main() == 0
    (tmp_path / "leaked.txt").write_text(secret)
    assert checker.main() == 1
    output = capsys.readouterr().out
    assert "openai-key: leaked.txt" in output
    assert secret not in output
