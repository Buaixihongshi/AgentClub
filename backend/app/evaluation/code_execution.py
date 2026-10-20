"""Execute stdin/stdout benchmark submissions in a fail-closed macOS sandbox."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


WORKER = '''import io, json, math, resource, sys
payload = json.load(sys.stdin)
resource.setrlimit(resource.RLIMIT_CPU, (payload["cpu"], payload["cpu"]))
resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
sys.stdin = io.StringIO(payload["stdin"])
exec(compile(payload["code"], "solution.py", "exec"), {"__name__": "__main__", "__file__": "solution.py"})
'''


def run_stdin(code: str, stdin: str, *, timeout: float = 4) -> dict:
    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        raise RuntimeError("A supported execution sandbox is required; refusing unsandboxed benchmark code")
    if not isinstance(code, str) or not isinstance(stdin, str) or timeout <= 0:
        raise ValueError("code/stdin must be text and timeout must be positive")
    executable = str(Path(sys.executable).resolve())
    with tempfile.TemporaryDirectory(prefix="yiren-code-check-") as directory:
        work = Path(directory).resolve()
        worker, stdout_path, stderr_path = (work / name for name in ("worker.py", "stdout.txt", "stderr.txt"))
        worker.write_text(WORKER)
        profile = "\n".join([
            "(version 1)", "(deny default)",
            f"(allow process-exec (subpath {json.dumps(str(Path(sys.base_prefix).resolve()))}))",
            "(allow sysctl-read)", "(allow mach-lookup)",
            # dyld/Python need system metadata outside /System and /usr. Permit
            # system reads, then exclude user data and other temporary jobs.
            "(allow file-read*)",
            '(deny file-read* (subpath "/Users") (subpath "/Volumes") (subpath "/private/var") '
            '(subpath "/private/tmp") (subpath "/tmp") (subpath "/var"))',
            f"(allow file-read* (subpath {json.dumps(str(work))}) "
            '(subpath "/private/var/db/dyld") (subpath "/private/var/db/timezone"))',
            "(allow file-write* " + " ".join(f"(literal {json.dumps(str(path))})" for path in (stdout_path, stderr_path))
            + ' (literal "/dev/null"))',
        ])
        command = ["sandbox-exec", "-p", profile, executable, "-I", "-S", str(worker)]
        payload = json.dumps({"code": code, "stdin": stdin, "cpu": max(1, math.ceil(timeout))})
        timed_out = False
        with stdout_path.open("w") as out, stderr_path.open("w") as err:
            try:
                result = subprocess.run(command, input=payload, text=True, stdout=out, stderr=err,
                                        cwd=work, env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "PYTHONHASHSEED": "0"}, timeout=timeout)
                returncode = result.returncode
            except subprocess.TimeoutExpired:
                timed_out, returncode = True, None
        return {"returncode": returncode, "timed_out": timed_out,
                "stdout": stdout_path.read_text(errors="replace"),
                "stderr": stderr_path.read_text(errors="replace")[:2000]}


def grade_stdin(code: str, tests: list[dict], *, timeout: float = 4) -> dict:
    results = []
    for index, test in enumerate(tests):
        if test.get("testtype") != "stdin":
            raise ValueError("This pilot only supports single-answer stdin tasks")
        result = run_stdin(code, test["input"], timeout=timeout)
        passed = result["returncode"] == 0 and not result["timed_out"] and result["stdout"].split() == test["output"].split()
        results.append({"index": index, "passed": passed, "timed_out": result["timed_out"],
                        "returncode": result["returncode"], "error": result["stderr"] if result["returncode"] else ""})
    count = sum(result["passed"] for result in results)
    return {"passed": bool(results) and count == len(results), "passed_tests": count,
            "total_tests": len(results), "test_fraction": count / len(results) if results else None, "checks": results}
