import json
import os
import re
import subprocess
import sys
from pathlib import Path

from backend.app.adapters.base import TurnOutcome
from backend.app.domain.models import TurnStatus
from backend.app.evaluation.a2a_probe import run_probes
from backend.app.evaluation.real_pilot import ROOT


class SubprocessFixture:
    """No model: drive the actual restricted MCP server over its stdio protocol."""
    def __init__(self, *args, **kwargs):
        self.db = kwargs["db_path"]
        self.artifacts = kwargs["artifact_root"]
        self.module = kwargs["mcp_module"]

    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        env = dict(os.environ, PYTHONPATH=str(ROOT), YIREN_MCP_DB_PATH=str(self.db),
                   YIREN_MCP_ARTIFACT_ROOT=str(self.artifacts), YIREN_MCP_MISSION_ID=mission_id,
                   YIREN_MCP_AGENT_ID=agent_id, YIREN_MCP_TURN_ID=turn_id,
                   YIREN_MCP_PROVIDER_ID=provider_id, YIREN_MCP_VALID_AGENTS="chulan,baobao",
                   YIREN_MCP_MAX_A2A_INVOCATIONS="1")
        process = subprocess.Popen([sys.executable, "-m", self.module], cwd=ROOT, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        request_id = 0

        def rpc(method, params):
            nonlocal request_id
            request_id += 1
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            response = json.loads(process.stdout.readline())
            assert "error" not in response, response
            return response["result"]

        def call(name, **arguments):
            result = rpc("tools/call", {"name": name, "arguments": arguments})
            assert not result.get("isError"), result

        try:
            rpc("initialize", {})
            advertised = rpc("tools/list", {})["tools"]
            assert {tool["name"] for tool in advertised} == {"publish_message", "read_context", "log_thought", "invoke_agent"}
            if agent_id == "baobao":
                receipt = re.search(r"签名回执=([a-f0-9]+)", prompt).group(1)
                call("publish_message", content=json.dumps({"rate_per_person_hour": 5, "receipt": receipt}))
            elif "\n【接续上一轮】" in prompt:
                receipt = re.search(r'"receipt":\s*"([a-f0-9]+)"', prompt).group(1)
                call("publish_message", content=json.dumps({"capacity": 105, "can_finish": True, "receipt": receipt}))
            elif "本题资料齐全" in prompt:
                receipt = re.search(r"签名回执为 ([a-f0-9]+)", prompt).group(1)
                call("publish_message", content=json.dumps({"capacity": 105, "can_finish": True, "receipt": receipt}))
            else:
                assert "签名回执=" not in prompt
                call("publish_message", content="正在核验缺失记录")
                call("invoke_agent", target_agent_id="baobao", purpose="查询处理速率与回执", mode="wait_reply")
        finally:
            process.stdin.close()
            process.wait(timeout=5)
            assert process.returncode == 0, process.stderr.read()
        tools.repository.add_event(mission_id, "turn.usage", {
            "turn_id": turn_id, "usage": {"input_tokens": 1000, "output_tokens": 100},
        })
        return TurnOutcome(status=TurnStatus.COMPLETED)


def test_probes_link_actual_subprocess_request_reply_and_resume(tmp_path):
    result = run_probes(tmp_path / "probe", execute=True, factory=SubprocessFixture)
    assert result["status"] == "completed"
    assert result["passed_probes"] == 6
    assert result["observed_host_calls"] == 14
    assert all(row["roundtrip_verified"] for row in result["results"][:4])
    assert all(row["invoke_tool_calls"] == 0 for row in result["results"][4:])
    for row in result["results"][:4]:
        assert row["reply_in_resume_prompt"]
        assert row["linked_reply"]
        assert row["final_correct"]


def test_probe_budget_stops_before_constructing_any_cli(tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Insufficient headroom must not start a CLI")
    result = run_probes(tmp_path / "probe", execute=True, limit_usd=0.001, factory=forbidden)
    assert result["status"] == "stopped"
    assert result["observed_host_calls"] == 0
    assert not result["results"][0]["passed"]


def test_dry_probe_does_not_access_models(tmp_path):
    result = run_probes(tmp_path / "probe")
    assert result["mode"] == "dry-run"
    assert result["max_host_calls"] == 14
    assert result["results"] == []
    assert not (tmp_path / "probe/calls.jsonl").exists()
