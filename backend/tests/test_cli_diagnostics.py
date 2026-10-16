import json

from backend.app.adapters import cli_diagnostics as diagnostics


def test_diagnostics_are_private_bounded_and_redacted(tmp_path, monkeypatch):
    directory = tmp_path / "logs"
    monkeypatch.setattr(diagnostics, "MAX_RECORDS", 2)
    for i in range(3):
        diagnostics.write_cli_diagnostic(
            directory, turn_id=str(i),
            stdout="x" * 40000 + '\n{"api_key": "sensitive-value"}\nBearer secret-bearer',
            stderr="password=secret-pass user@example.com sk-private-key",
        )
    records = list(directory.glob("*.json"))
    assert len(records) == 2
    assert directory.stat().st_mode & 0o777 == 0o700
    for path in records:
        assert path.stat().st_mode & 0o777 == 0o600
        data = json.loads(path.read_text())
        assert len(data["stdout_tail"]) <= diagnostics.MAX_STREAM_CHARS
        assert data["stdout_truncated"] is True
        for secret in ("sensitive-value", "secret-bearer", "secret-pass", "user@example.com", "sk-private-key"):
            assert secret not in path.read_text()


def test_diagnostic_io_failure_is_nonfatal(tmp_path):
    file = tmp_path / "not-a-directory"
    file.write_text("keep")
    diagnostics.write_cli_diagnostic(file, stdout="out", stderr="err")
    assert file.read_text() == "keep"
