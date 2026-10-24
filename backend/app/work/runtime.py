"""Host-side filesystem scope used by work CLI and validation commands."""
import json
import os
import subprocess
import tempfile
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

from backend.app.adapters.deliberation_cli import isolation_supported, sandbox_profile
from backend.app.adapters.subprocess_cli import kill_process_tree
from backend.app.work.service import WorkError


@contextmanager
def managed_process(process):
    """A host crash closes the pipe and stops the entire child process group."""
    read_fd, write_fd = os.pipe()
    try:
        watcher = subprocess.Popen([sys.executable,
            str(Path(__file__).resolve().parents[1] / 'adapters/process_watchdog.py'), str(read_fd), str(process.pid)],
            pass_fds=(read_fd,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except BaseException:
        os.close(write_fd)
        kill_process_tree(process)
        raise
    finally:
        os.close(read_fd)
    try:
        yield process
    finally:
        os.close(write_fd)
        kill_process_tree(process)
        try:
            watcher.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def profile(directory, executable, root, *, delivery, readonly=False, protected_paths=(), auth_paths=(), loopback_ports=()):
    root = Path(root).resolve()
    original_executable = Path(shutil.which(executable) or executable).absolute()
    runtime = original_executable.parent.parent
    runtime_paths = [runtime] if (runtime / 'pyvenv.cfg').is_file() else []
    # Extend the existing tested OS profile, excluding the authorized workspace
    # from the broad HOME-read deny while keeping runtime DB and secrets hidden.
    base = sandbox_profile(directory, executable, auth_paths=[*auth_paths, root, *runtime_paths],
                           protected_paths=protected_paths, loopback_ports=loopback_ports)
    # auth_paths are literal exceptions in the original profile; add subpath
    # exception explicitly for a complete project read tree.
    base = base.replace(f'(require-not (literal {json.dumps(str(root))}))',
                        f'(require-not (subpath {json.dumps(str(root))}))')
    for path in runtime_paths:
        base = base.replace(f'(require-not (literal {json.dumps(str(path))}))',
                            f'(require-not (subpath {json.dumps(str(path))}))')
    lines = [base]
    if not readonly:
        writable = [root / 'docs'] if delivery == 'document' else [root]
        lines.extend(f'(allow file-write* (subpath {json.dumps(str(p))}))' for p in writable)
    # Local commits/merges and runtime data mutation are separate authority.
    for path in [root / '.git', root / '.agenthub', *protected_paths]:
        lines.append(f'(deny file-write* (subpath {json.dumps(str(Path(path).resolve()))}))')
    for path in protected_paths:
        lines.append(f'(deny file-read* (subpath {json.dumps(str(Path(path).resolve()))}))')
    return '\n'.join(lines)


def run_check(root, command, timeout, *, delivery='development', protected_paths=(), on_spawn=None):
    if not isolation_supported():
        raise WorkError('当前平台不支持受限验证执行，未运行命令；请配置支持隔离的执行环境')
    with tempfile.TemporaryDirectory(prefix='acwork-check-', dir='/tmp') as temp:
        directory = Path(temp).resolve()
        policy = directory / 'check.sb'
        policy.write_text(profile(directory, command[0], root, delivery=delivery, protected_paths=protected_paths))
        env = {k: v for k, v in os.environ.items() if not k.startswith('YIREN_')}
        env.update(TMPDIR=str(directory), TMP=str(directory), TEMP=str(directory),
                   XDG_CACHE_HOME=str(directory / 'cache'), PYTHONDONTWRITEBYTECODE='1')
        process = subprocess.Popen(['/usr/bin/sandbox-exec', '-f', str(policy), *command], cwd=root,
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        with managed_process(process):
            if on_spawn:
                on_spawn(process.pid)
            try:
                output, _ = process.communicate(timeout=timeout)
                return output, process.returncode
            except subprocess.TimeoutExpired:
                kill_process_tree(process)
                output, _ = process.communicate()
                return output + b'\nValidation timed out', -1
