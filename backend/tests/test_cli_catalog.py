import json
import subprocess

import pytest

from backend.app.adapters import cli_catalog
from backend.app.domain.models import CLIProvider


def _provider(pid, command='fakecli'):
    return CLIProvider(id=pid, label=pid.title(), command=command)


@pytest.fixture(autouse=True)
def _clear_cache():
    cli_catalog._cache.clear()
    yield
    cli_catalog._cache.clear()


def _completed(stdout, code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout.encode(), stderr=b'')


def test_codex_json_keeps_listed_models_in_priority_order():
    output = json.dumps({'models': [
        {'slug': 'b', 'display_name': 'B', 'visibility': 'list', 'priority': 2},
        {'slug': 'hidden', 'display_name': 'H', 'visibility': 'hide', 'priority': 0},
        {'slug': 'a', 'display_name': 'A', 'visibility': 'list', 'priority': 1},
        {'slug': 'bad\nid', 'visibility': 'list'},
    ]})
    assert [m['id'] for m in cli_catalog.parse_codex_json(output)] == ['a', 'b']


def test_tab_separated_skips_progress_lines():
    output = 'Fetching available models...\ngemini-x\tGemini X\nclaude-y\tClaude Y\n'
    assert cli_catalog.parse_tab_separated(output) == [
        {'id': 'gemini-x', 'label': 'Gemini X', 'vendor': None},
        {'id': 'claude-y', 'label': 'Claude Y', 'vendor': None},
    ]


def test_claude_uses_fixed_aliases_without_running_a_command(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('must not run'))
    result = cli_catalog.cli_catalog(_provider('claude'))
    assert result['status'] == 'ready'
    assert [m['id'] for m in result['models']] == ['fable', 'opus', 'sonnet']


def test_listing_runs_once_and_is_cached_until_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(cli_catalog.shutil, 'which', lambda command: '/usr/bin/' + command)

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs['stdin'] is subprocess.DEVNULL and kwargs['timeout'] == cli_catalog.CATALOG_TIMEOUT_SECONDS
        return _completed('m1\tModel 1\n')

    monkeypatch.setattr(cli_catalog.subprocess, 'run', run)
    provider = _provider('antigravity', 'agy')
    first = cli_catalog.cli_catalog(provider)
    assert first['status'] == 'ready' and first['models'][0]['id'] == 'm1'
    assert cli_catalog.cli_catalog(provider) == first
    assert calls == [['agy', 'models']]
    cli_catalog.cli_catalog(provider, refresh=True)
    assert len(calls) == 2


def test_failed_refresh_keeps_previous_models_as_stale(monkeypatch):
    monkeypatch.setattr(cli_catalog.shutil, 'which', lambda command: '/usr/bin/' + command)
    outputs = iter([_completed('{"models":[{"slug":"m1","visibility":"list"}]}'), _completed('', code=2)])
    monkeypatch.setattr(cli_catalog.subprocess, 'run', lambda *a, **k: next(outputs))
    provider = _provider('codex', 'codex')
    assert cli_catalog.cli_catalog(provider)['status'] == 'ready'
    stale = cli_catalog.cli_catalog(provider, refresh=True)
    assert stale['status'] == 'stale' and stale['models'][0]['id'] == 'm1'
    assert '退出码 2' in stale['error']


def test_missing_command_and_unknown_cli_report_without_running(monkeypatch):
    monkeypatch.setattr(cli_catalog.shutil, 'which', lambda command: None)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('must not run'))
    missing = cli_catalog.cli_catalog(_provider('codex', 'codex'))
    assert missing['status'] == 'error' and '未找到命令 codex' in missing['error']
    unknown = cli_catalog.cli_catalog(_provider('custom'))
    assert unknown['status'] == 'unsupported' and unknown['models'] == []
