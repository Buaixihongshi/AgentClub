import base64
import json
import pickle
import sys
import zlib

import pytest

from backend.app.evaluation.code_execution import grade_stdin, run_stdin
from scripts.prepare_open_code_pilot import decode_private


@pytest.mark.skipif(sys.platform != "darwin", reason="This worker requires macOS sandbox-exec")
def test_stdin_grader_accepts_whitespace_but_rejects_extra_or_wrong_output():
    tests = [{"testtype": "stdin", "input": "2 3", "output": "5\n"}]
    assert grade_stdin("a,b=map(int,input().split());print(a+b)", tests)["passed"]
    assert not grade_stdin("print('answer: 5')", tests)["passed"]
    assert not grade_stdin("print(6)", tests)["passed"]


@pytest.mark.skipif(sys.platform != "darwin", reason="This worker requires macOS sandbox-exec")
def test_execution_cannot_read_external_job_data_or_modify_it(tmp_path):
    canary = tmp_path / "private-canary.txt"
    canary.write_text("private-grader-data")
    result = run_stdin(f"print(open({str(canary)!r}).read())", "")
    assert result["returncode"] != 0
    assert "private-grader-data" not in result["stdout"]
    result = run_stdin(f"open({str(canary)!r},'w').write('changed')", "")
    assert result["returncode"] != 0
    assert canary.read_text() == "private-grader-data"


@pytest.mark.skipif(sys.platform != "darwin", reason="This worker requires macOS sandbox-exec")
def test_execution_denies_network_and_times_out_infinite_loop():
    result = run_stdin("import socket\ns=socket.socket();s.bind(('127.0.0.1',0));print('bound')", "")
    assert result["returncode"] != 0
    assert "bound" not in result["stdout"]
    assert run_stdin("while True: pass", "", timeout=0.2)["timed_out"]


def test_private_case_decoder_accepts_data_only_and_rejects_executable_pickle():
    cases = [{"testtype": "stdin", "input": "1", "output": "2"}]
    compressed = base64.b64encode(zlib.compress(pickle.dumps(json.dumps(cases)))).decode()
    assert decode_private(compressed) == cases
    assert decode_private(json.dumps(cases)) == cases
    executable_pickle = b"cos\nsystem\n(S'echo should-not-execute'\ntR."
    malicious = base64.b64encode(zlib.compress(executable_pickle)).decode()
    with pytest.raises(ValueError, match="globals"):
        decode_private(malicious)
