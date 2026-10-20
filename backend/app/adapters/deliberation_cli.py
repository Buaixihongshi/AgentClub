"""Restricted CLI execution with a host-owned MCP bridge.

The provider never receives a database path or a DB-capable MCP subprocess.
The sandbox can reach its own bridge and remote model APIs, but cannot read
the repository, peer workspaces, transcripts or the local HTTP application.
"""
import json
import hashlib
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from backend.app.adapters.base import TurnOutcome
from backend.app.adapters.deliberation_errors import failure_code, private_error_tail
from backend.app.config.providers import get_cli_provider
from backend.app.domain.models import TurnStatus
from backend.app.mcp.server import _Context, _dispatch


def isolation_supported():
    return platform.system() == "Darwin" and Path("/usr/bin/sandbox-exec").is_file()


PROXY_SOURCE = '''import json,socket,sys
with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
    s.connect(sys.argv[1])
    stream=s.makefile("rwb")
    for line in sys.stdin.buffer:
        stream.write(line);stream.flush()
        try: frame=json.loads(line)
        except ValueError: frame={"id":None}
        if "id" in frame:
            reply=stream.readline()
            if not reply: break
            sys.stdout.buffer.write(reply);sys.stdout.buffer.flush()
'''


@contextmanager
def mcp_bridge(directory, tools, mission_id, agent_id, turn_id, provider_id):
    path = directory / "mcp.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    os.chmod(path, 0o600)
    listener.listen(4)
    listener.settimeout(0.2)
    stopped = threading.Event()
    connections = []
    context = _Context(mission_id, agent_id, turn_id, provider_id, tools)

    def serve(connection):
        try:
            with connection, connection.makefile("rwb") as stream:
                while line := stream.readline(512_001):
                    if len(line) > 512_000:
                        break
                    try:
                        frame = json.loads(line)
                        reply = _dispatch(context, frame)
                    except (ValueError, TypeError):
                        reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}}
                    if reply is not None:
                        stream.write((json.dumps(reply, ensure_ascii=False) + "\n").encode())
                        stream.flush()
        except OSError:
            pass

    def accept():
        while not stopped.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            connections.append(connection)
            threading.Thread(target=serve, args=(connection,), daemon=True).start()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    proxy = directory / "mcp_proxy.py"
    proxy.write_text(PROXY_SOURCE)
    try:
        yield {"command": str(Path(sys.executable).resolve()), "args": ["-I", "-S", str(proxy), str(path)]}
    finally:
        stopped.set()
        listener.close()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        thread.join(timeout=1)


def sandbox_profile(directory, executable, *, auth_paths=(), protected_paths=(), loopback_ports=()):
    """Protect user/runtime data while allowing the OS runtime to start.

    macOS also reads its boot-volume metadata outside /System and /usr during
    exec. Keep those OS reads available, explicitly excluding user data and
    shared temporary directories. All writes remain default-denied.
    """
    quote = lambda value: json.dumps(str(value))
    readable = []
    executable_path = Path(shutil.which(executable) or executable).resolve()
    # User-installed executables need their own package runtime, never all HOME.
    node = shutil.which("node")
    if node:
        node_root = Path(node).resolve().parent.parent
        if node_root.is_relative_to('/Users') and '/.nvm/versions/node/' in str(node_root):
            readable.append(str(node_root))
    exclusions = [f'(require-not (subpath {quote(directory)}))']
    exclusions.append(f'(require-not (literal {quote(executable_path)}))')
    exclusions += [f'(require-not (subpath {quote(p)}))' for p in readable]
    exclusions += [f'(require-not (literal {quote(p)}))' for p in auth_paths]
    lines = [
        "(version 1)", "(deny default)",
        "(allow process-exec process-fork sysctl-read mach-lookup mach-register)",
        "(allow file-read*)",
        '(allow file-read* file-write* (subpath "/dev"))',
        f"(allow file-read* file-write* (subpath {quote(directory)}))",
        '(allow network-outbound (require-all (remote ip "*:*") (require-not (remote ip "localhost:*"))))',
        '(allow network-outbound (remote unix-socket (path-literal "/private/var/run/mDNSResponder")))',
        f'(allow network-outbound (remote unix-socket (path-literal {quote(directory / "mcp.sock")})))',
    ]
    exceptions = ' '.join(f'(require-not (remote ip "localhost:{int(port)}"))' for port in loopback_ports)
    lines.append(f'(deny network-outbound (require-all (remote ip "localhost:*") {exceptions}))')
    lines += [f'(allow network-outbound (remote ip "localhost:{int(port)}"))' for port in loopback_ports]
    if Path(executable).name == 'codex':
        # CFPreferencesAppSynchronize needs the OS preference cache, even
        # when no managed values exist. No preference writes are permitted.
        lines += ['(allow user-preference-read (preference-domain "com.openai.codex"))',
                  '(allow ipc-posix-shm-read* (ipc-posix-name-prefix "apple.cfprefs."))']
    if Path(executable).name == 'agy':
        lines.append('(allow network-bind network-inbound (local ip "localhost:*"))')
    for protected in ['/Users', '/Volumes', '/private/var/folders', '/private/tmp', '/tmp', '/private/var/root', *protected_paths]:
        lines.append(f'(deny file-read-data (require-all (subpath {quote(protected)}) {" ".join(exclusions)}))')
    return "\n".join(lines)


def prompt_arguments(basename, args, prompt):
    if basename == 'codex':
        return [*args, prompt]
    flags = {'-p', '--print'} | ({'--prompt'} if basename == 'agy' else set())
    if any(arg.startswith(tuple(flag + '=' for flag in flags)) for arg in args):
        raise ValueError('V2 supplies its own print prompt; remove configured prompt values')
    options = [arg for arg in args if arg not in flags]
    # agy consumes the next token after -p; bind explicitly so additional
    # options can never become its prompt. Claude's -p is a boolean flag.
    return [*options, '-p=' + prompt] if basename == 'agy' else [*options, '-p', prompt]


def claude_oauth_token(env):
    """Read only the selected CLI credential on the host, never its sessions.

    The child receives an access token, not access to the user's Keychain.
    Existing API-key/token configuration always takes precedence.
    """
    if any(env.get(key) for key in ('CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')):
        return None
    config = Path(env.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    try:
        data = json.loads((config / '.credentials.json').read_text())
    except (OSError, ValueError):
        secure_dir = env.get('CLAUDE_SECURESTORAGE_CONFIG_DIR', env.get('CLAUDE_CONFIG_DIR', ''))
        suffix = '-' + hashlib.sha256(secure_dir.encode()).hexdigest()[:8] if secure_dir else ''
        try:
            result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-a', env.get('USER', ''),
                                     '-w', '-s', 'Claude Code-credentials' + suffix],
                                    capture_output=True, text=True, timeout=5)
            data = json.loads(result.stdout) if result.returncode == 0 else {}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            data = {}
    oauth = data.get('claudeAiOauth') if isinstance(data, dict) else None
    token = oauth.get('accessToken') if isinstance(oauth, dict) else None
    return token if isinstance(token, str) and token else None


def claude_provider_environment(env):
    """Retain provider connection settings without loading hooks or projects."""
    config = Path(env.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    try:
        values = json.loads((config / 'settings.json').read_text()).get('env', {})
    except (OSError, ValueError, AttributeError):
        return
    allowed = {'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL',
               'ANTHROPIC_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL'}
    if isinstance(values, dict):
        for key, value in values.items():
            if (key in allowed or key.startswith('ANTHROPIC_DEFAULT_')) and isinstance(value, str):
                env.setdefault(key, value)


class DeliberationCLIAdapter:
    def __init__(self, config_dir, timeout, provider_overrides):
        self.config_dir = config_dir
        self.timeout = timeout
        self.provider_overrides = provider_overrides

    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        if not isolation_supported():
            return TurnOutcome(status=TurnStatus.BLOCKED, summary="isolation_not_supported")
        provider = get_cli_provider(self.config_dir, provider_id, overrides=self.provider_overrides)
        if not provider.enabled:
            return TurnOutcome(status=TurnStatus.BLOCKED, summary="Provider is disabled")
        basename = Path(provider.command).name
        if basename not in {"codex", "claude", "agy"}:
            return TurnOutcome(status=TurnStatus.BLOCKED, summary="isolation_not_supported for this CLI")
        with tempfile.TemporaryDirectory(prefix="acv2-", dir="/tmp") as temp:
            directory = Path(temp).resolve()
            with mcp_bridge(directory, tools, mission_id, agent_id, turn_id, provider_id) as server:
                env = {k: v for k, v in os.environ.items() if not k.startswith("YIREN_")}
                env.pop("PYTHONPATH", None)
                env.pop("NODE_OPTIONS", None)
                env.update(TMPDIR=str(directory), TMP=str(directory), TEMP=str(directory),
                           CLAUDE_CODE_TMPDIR=str(directory), XDG_CACHE_HOME=str(directory / 'cache'),
                           XDG_STATE_HOME=str(directory / 'state'), XDG_DATA_HOME=str(directory / 'data'))
                auth = []
                loopback_ports = []
                args = list(provider.args)
                if basename == "claude":
                    config = directory / "mcp.json"
                    config.write_text(json.dumps({"mcpServers": {"yiren_hub": server}}))
                    settings = directory / 'claude-settings.json'
                    settings.write_text(json.dumps({'disableAllHooks': True}))
                    args = ["--mcp-config", str(config), "--strict-mcp-config", "--tools", "",
                            '--setting-sources', '', '--settings', str(settings), *args]
                    if '--no-session-persistence' not in args:
                        args.append('--no-session-persistence')
                    claude_provider_environment(env)
                    endpoint = urlsplit(env.get('ANTHROPIC_BASE_URL', ''))
                    if endpoint.hostname in {'localhost', '127.0.0.1', '::1'}:
                        port = endpoint.port or (443 if endpoint.scheme == 'https' else 80)
                        if port in {8000, 5173}:
                            raise ValueError('Provider endpoint cannot be the local AgentClub application')
                        loopback_ports.append(port)
                    token = claude_oauth_token(env)
                    if token:
                        env['CLAUDE_CODE_OAUTH_TOKEN'] = token
                    claude_home = directory / 'claude'
                    claude_home.mkdir()
                    env['CLAUDE_CONFIG_DIR'] = str(claude_home)
                    env['CLAUDE_SECURESTORAGE_CONFIG_DIR'] = str(claude_home)
                elif basename == "codex":
                    env["CODEX_HOME"] = str(directory)
                    credentials = Path.home() / ".codex/auth.json"
                    if credentials.is_file():
                        shutil.copy2(credentials, directory / "auth.json")
                    config = directory / "config.toml"
                    config.write_text(
                        f'[projects.{json.dumps(str(directory))}]\ntrust_level="trusted"\n'
                        f'[mcp_servers.yiren_hub]\ncommand={json.dumps(server["command"])}\n'
                        f'args={json.dumps(server["args"])}\n'
                    )
                    if "--skip-git-repo-check" not in args:
                        args.append("--skip-git-repo-check")
                else:
                    agy_home = directory / 'home'
                    agy_config = agy_home / '.gemini/config'
                    agy_config.mkdir(parents=True)
                    (agy_config / 'mcp_config.json').write_text(json.dumps({'mcpServers': {'yiren_hub': server}}))
                    for name in ('installation_id', 'jetski_state.pbtxt'):
                        identity = Path.home() / '.gemini/antigravity-cli' / name
                        if identity.is_file():
                            target = agy_home / '.gemini/antigravity-cli' / name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(identity, target)
                    try:
                        user_settings = json.loads((Path.home() / '.gemini/antigravity-cli/settings.json').read_text())
                        routing = {key: user_settings[key] for key in ('model', 'useG1Credits')
                                   if key in user_settings and isinstance(user_settings[key], (str, bool))}
                        target = agy_home / '.gemini/antigravity-cli/settings.json'
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(json.dumps(routing))
                    except (OSError, ValueError, TypeError):
                        pass
                    env['HOME'] = str(agy_home)
                    # Security.framework derives the default search path from
                    # HOME. Keep its existing unlocked login Keychain visible
                    # through a read-only alias; session/config data stays local.
                    keychain = Path.home() / 'Library/Keychains/login.keychain-db'
                    if keychain.is_file():
                        alias = agy_home / 'Library/Keychains/login.keychain-db'
                        alias.parent.mkdir(parents=True)
                        alias.symlink_to(keychain)
                    args += ["--new-project", "--disable-slash-commands", "--log-file", str(directory / "agy.log")]
                    auth += [Path.home() / 'Library/Preferences/com.apple.security.plist',
                             Path.home() / 'Library/Keychains/login.keychain-db']
                profile = directory / "isolation.sb"
                if any(a in {'--resume', '--continue', '--conversation', 'resume'} for a in args) or (
                    basename in {'claude', 'agy'} and any(a in {'-c', '-r'} for a in args)
                ):
                    return TurnOutcome(status=TurnStatus.BLOCKED, summary='V2 cannot resume an existing CLI conversation')
                profile.write_text(sandbox_profile(directory, provider.command, auth_paths=auth, protected_paths=[
                    tools.repository.database_path.resolve().parent, tools.artifact_root.resolve(), Path.cwd().resolve()],
                    loopback_ports=loopback_ports))
                command = ["/usr/bin/sandbox-exec", "-f", str(profile), provider.command,
                           *prompt_arguments(basename, args, prompt)]
                proc = subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                deadline = time.monotonic() + self.timeout
                terminated = None
                while True:
                    try:
                        stdout, stderr = proc.communicate(timeout=min(.25, max(.01, deadline - time.monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        from backend.app.orchestration.deliberation import Deliberation
                        state = Deliberation(tools.repository).state(tools.repository.get_mission(mission_id))
                        cancelled = state['pending_control'] == 'stop' or state['phase'] == 'ended'
                        if cancelled or time.monotonic() >= deadline:
                            from backend.app.adapters.subprocess_cli import kill_process_tree
                            kill_process_tree(proc)
                            stdout, stderr = proc.communicate(timeout=5)
                            terminated = 'Discussion stopped' if cancelled else 'Isolated CLI timed out'
                            break
                from backend.app.adapters.stream_parsers import StreamLineParser
                from backend.app.adapters.mcp_subprocess import _emit_turn_usage
                parser = StreamLineParser(provider_id)
                usage = None
                stream_errors = []
                for line in stdout.decode(errors='replace').splitlines():
                    event = parser.parse(line)
                    if event and event.usage:
                        usage = event.usage
                    try:
                        frame = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(frame, dict) and frame.get('type') == 'result' and frame.get('is_error'):
                        errors = frame.get('errors') or [frame.get('result')]
                        if isinstance(errors, str):
                            errors = [errors]
                        stream_errors.extend(str(item) for item in errors if item)
                _emit_turn_usage(tools.repository, mission_id, agent_id, provider_id, turn_id, usage)
                from backend.app.orchestration.deliberation import Deliberation
                accepted = Deliberation(tools.repository).state(tools.repository.get_mission(mission_id))['leases'][turn_id]['status'] == 'submitted'
                diagnostic = terminated or '\n'.join([stderr.decode(errors='replace'), *stream_errors])
                if not accepted and basename == 'agy':
                    log = directory / 'agy.log'
                    if log.is_file():
                        diagnostic += '\n' + log.read_text(errors='replace')[-8000:]
                code = None if accepted else failure_code(terminated or diagnostic, exit_code=proc.returncode)
                tools.repository.add_event(mission_id, "discussion.provider_execution", {
                    "turn_id": turn_id, "provider_id": provider_id, "exit_code": proc.returncode,
                    "isolation": "macos-sandbox+host-mcp-bridge", "usage_available": usage is not None,
                    "failure_code": code,
                })
                # Success requires an accepted structured submission, checked by
                # the runner. stdout never becomes a chat message or a result.
                error = 'Structured result accepted' if accepted else (private_error_tail(diagnostic) or 'No structured result submitted')
                return TurnOutcome(status=TurnStatus.COMPLETED if not proc.returncode and not terminated else TurnStatus.BLOCKED,
                                   summary=error, failure_code=code, exit_code=proc.returncode)
