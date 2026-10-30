import json
import subprocess
from pathlib import Path

import pytest

from backend.app.extensions.discussion.deliberation_cli import claude_oauth_token, claude_provider_environment, prompt_arguments
from backend.app.adapters.deliberation_errors import failure_code, private_error_tail


@pytest.mark.parametrize('flag', ['-p', '--print', '--prompt'])
def test_agy_prompt_is_bound_after_all_runtime_options(flag):
    prompt = '--new-project 是讨论数据\n换行也不能变成选项'
    args = prompt_arguments('agy', ['--dangerously-skip-permissions', flag,
                                  '--new-project', '--log-file', 'own.log'], prompt)
    assert args[-1] == '-p=' + prompt
    assert args.count('--new-project') == 1
    assert flag not in args


def test_claude_print_flag_is_boolean_and_precedes_real_prompt():
    assert prompt_arguments('claude', ['-p', '--no-session-persistence'], 'hello') == [
        '--no-session-persistence', '-p', 'hello']
    with pytest.raises(ValueError):
        prompt_arguments('agy', ['--prompt=stale user input'], 'new input')


def test_provider_settings_copy_connection_fields_without_hooks_or_environment_injection(tmp_path):
    (tmp_path / 'settings.json').write_text(json.dumps({'hooks': {'danger': 'not loaded'}, 'env': {
        'ANTHROPIC_AUTH_TOKEN': 'fake-auth', 'ANTHROPIC_BASE_URL': 'http://127.0.0.1:1234',
        'ANTHROPIC_DEFAULT_SONNET_MODEL': 'custom-model', 'NODE_OPTIONS': '--require=evil',
        'YIREN_DATABASE_PATH': 'private.sqlite3', 'CODEX_HOME': 'other-session',
    }}))
    env = {'CLAUDE_CONFIG_DIR': str(tmp_path), 'ANTHROPIC_AUTH_TOKEN': 'shell-auth'}
    claude_provider_environment(env)
    assert env == {'CLAUDE_CONFIG_DIR': str(tmp_path), 'ANTHROPIC_AUTH_TOKEN': 'shell-auth',
                   'ANTHROPIC_BASE_URL': 'http://127.0.0.1:1234', 'ANTHROPIC_DEFAULT_SONNET_MODEL': 'custom-model'}
    assert claude_oauth_token(env) is None


def test_selected_oauth_credential_is_read_privately_and_existing_auth_wins(tmp_path, monkeypatch):
    (tmp_path / '.credentials.json').write_text(json.dumps({'claudeAiOauth': {'accessToken': 'fake-oauth'}}))
    assert claude_oauth_token({'CLAUDE_CONFIG_DIR': str(tmp_path)}) == 'fake-oauth'
    assert claude_oauth_token({'CLAUDE_CONFIG_DIR': str(tmp_path), 'ANTHROPIC_API_KEY': 'fake-key'}) is None
    (tmp_path / '.credentials.json').unlink()
    calls = []
    def keychain(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"claudeAiOauth":{"accessToken":"fake-keychain"}}', '')
    monkeypatch.setattr(subprocess, 'run', keychain)
    assert claude_oauth_token({'USER': 'test', 'CLAUDE_SECURESTORAGE_CONFIG_DIR': ''}) == 'fake-keychain'
    assert calls[0][-1] == 'Claude Code-credentials'


def test_fatal_configuration_error_wins_over_secondary_warning_and_keeps_its_tail():
    text = 'application network policy is unavailable\n' + 'warning\n' * 300 + 'Error: Failed to synchronize managed preferences'
    assert failure_code(text) == 'managed_preferences'
    assert private_error_tail(text).endswith('Failed to synchronize managed preferences')
    assert len(private_error_tail(text)) <= 1000
