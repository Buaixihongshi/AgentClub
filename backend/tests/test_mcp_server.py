import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from backend.app import extension_points
from backend.app.domain.models import Conversation, Mission
from backend.app.storage.repository import Repository


REPO_ROOT = Path(__file__).resolve().parents[2]


class MCPServerHarness:
    """Spawn the stdio MCP server and exchange JSON-RPC frames over its pipes."""

    def __init__(
        self,
        *,
        db_path: Path,
        artifact_root: Path,
        mission_id: str,
        agent_id: str,
        turn_id: Optional[str],
        provider_id: str,
        timeout: float = 5.0,
        extra_env: Optional[Dict[str, str]] = None,
    ):
        self.timeout = timeout
        env = os.environ.copy()
        server_env = {
            "PYTHONPATH": str(REPO_ROOT),
            "YIREN_MCP_DB_PATH": str(db_path),
            "YIREN_MCP_ARTIFACT_ROOT": str(artifact_root),
            "YIREN_MCP_MISSION_ID": mission_id,
            "YIREN_MCP_AGENT_ID": agent_id,
            "YIREN_MCP_PROVIDER_ID": provider_id,
            # The parent adapters pass the extensions they installed.
            "YIREN_MCP_EXTENSIONS": ",".join(extension_points.modules()),
        }
        if turn_id is not None:
            server_env["YIREN_MCP_TURN_ID"] = turn_id
        else:
            env.pop("YIREN_MCP_TURN_ID", None)
        env.update(server_env)
        if extra_env:
            env.update(extra_env)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "backend.app.mcp.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(REPO_ROOT),
            env=env,
        )

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, *, request_id: int = 1) -> Dict[str, Any]:
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            frame["params"] = params
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(frame) + "\n")
        self.proc.stdin.flush()
        return self._read_response_for_id(request_id)

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        frame = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            frame["params"] = params
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(frame) + "\n")
        self.proc.stdin.flush()

    def _read_response_for_id(self, request_id: int) -> Dict[str, Any]:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                stderr_text = ""
                if self.proc.stderr is not None:
                    try:
                        stderr_text = self.proc.stderr.read()
                    except Exception:
                        stderr_text = ""
                raise AssertionError(
                    "MCP server closed stdout without responding. stderr=" + stderr_text
                )
            payload = json.loads(line)
            if payload.get("id") == request_id:
                return payload

    def close(self) -> str:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
            self.proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=self.timeout)
        stderr = ""
        if self.proc.stderr is not None:
            try:
                stderr = self.proc.stderr.read()
            except Exception:
                stderr = ""
        return stderr


@pytest.fixture()
def seeded_mission(repository: Repository):
    conv = Conversation(id="c_test", title="t")
    repository.create_conversation(conv)
    mission = Mission(
        id="m_test",
        title="Plan",
        goal="ship feature",
        conversation_id=conv.id,
    )
    repository.create_mission(mission)
    return mission


def _spawn(
    temp_settings,
    mission,
    *,
    turn_id="t_1",
    agent_id="baobao",
    provider_id="codex",
    extra_env: Optional[Dict[str, str]] = None,
) -> MCPServerHarness:
    return MCPServerHarness(
        db_path=temp_settings.database_path,
        artifact_root=temp_settings.artifact_root,
        mission_id=mission.id,
        agent_id=agent_id,
        turn_id=turn_id,
        provider_id=provider_id,
        extra_env=extra_env,
    )


def test_initialize_returns_protocol_and_tool_capability(temp_settings, seeded_mission):
    harness = _spawn(temp_settings, seeded_mission)
    try:
        response = harness.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
        )
    finally:
        harness.close()

    assert response.get("jsonrpc") == "2.0"
    assert response.get("id") == 1
    result = response.get("result")
    assert isinstance(result, dict), response
    assert isinstance(result.get("protocolVersion"), str)
    assert result.get("serverInfo", {}).get("name") == "yiren-hub"
    assert "tools" in result.get("capabilities", {})


def test_tools_list_includes_publish_message(temp_settings, seeded_mission):
    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request("tools/list", {}, request_id=2)
    finally:
        harness.close()

    tools = response["result"]["tools"]
    publish = next((t for t in tools if t["name"] == "publish_message"), None)
    assert publish is not None, tools
    schema = publish["inputSchema"]
    assert schema["type"] == "object"
    assert "content" in schema["properties"]
    assert "content" in schema.get("required", [])
    publish_descriptor = json.dumps(publish, ensure_ascii=False)
    assert "120 字以内" not in publish_descriptor
    assert "120字以内" not in publish_descriptor


def test_publish_message_writes_agent_message_into_repository(temp_settings, seeded_mission, repository):
    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request(
            "tools/call",
            {"name": "publish_message", "arguments": {"content": "你好老板，我先草拟一下方案。"}},
            request_id=2,
        )
    finally:
        stderr = harness.close()

    result = response.get("result")
    assert result is not None, (response, stderr)
    assert result.get("isError") is not True, result
    assert result["content"][0]["type"] == "text"

    messages = repository.list_messages(seeded_mission.id)
    agent_messages = [m for m in messages if m.role.value == "agent"]
    assert len(agent_messages) == 1
    msg = agent_messages[0]
    assert msg.content == "你好老板，我先草拟一下方案。"
    assert msg.agent_id == "baobao"
    assert msg.turn_id == "t_1"
    assert msg.provider_id == "codex"
    assert msg.conversation_id == seeded_mission.conversation_id

    events = repository.list_events_after(seeded_mission.id, 0)
    tool_calls = [e for e in events if e.event_type == "tool.call"]
    tool_results = [e for e in events if e.event_type == "tool.result"]
    assert any(e.payload.get("tool_name") == "publish_message" for e in tool_calls)
    assert any(e.payload.get("tool_name") == "publish_message" for e in tool_results)


def test_publish_message_returns_jsonrpc_error_for_unknown_mission(temp_settings, seeded_mission):
    harness = MCPServerHarness(
        db_path=temp_settings.database_path,
        artifact_root=temp_settings.artifact_root,
        mission_id="m_does_not_exist",
        agent_id="baobao",
        turn_id="t_1",
        provider_id="codex",
    )
    try:
        harness.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request(
            "tools/call",
            {"name": "publish_message", "arguments": {"content": "hi"}},
            request_id=2,
        )
    finally:
        harness.close()

    result = response.get("result")
    if result is not None:
        assert result.get("isError") is True, response
        assert "Unknown mission" in result["content"][0]["text"]
    else:
        assert "error" in response, response


def test_unknown_tool_returns_error(temp_settings, seeded_mission):
    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request(
            "tools/call",
            {"name": "delete_database", "arguments": {}},
            request_id=2,
        )
    finally:
        harness.close()

    assert "error" in response or response.get("result", {}).get("isError") is True


def test_tools_list_includes_all_tools(temp_settings, seeded_mission):
    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request("tools/list", {}, request_id=2)
    finally:
        harness.close()

    tool_names = {t["name"] for t in response["result"]["tools"]}
    assert tool_names == {
        "shared_work",
        "publish_message",
        "memory",
        "save_checkpoint",
        "read_context",
        "write_artifact",
        "read_artifact",
        "update_status",
        "invoke_agent",
        "log_thought",
    }


def test_tools_list_hides_shared_work_when_tasks_extension_off(temp_settings, seeded_mission):
    harness = _spawn(temp_settings, seeded_mission, extra_env={"YIREN_MCP_TASKS_ENABLED": "0"})
    try:
        harness.request(
            "initialize",
            {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            request_id=1,
        )
        harness.notify("notifications/initialized")
        listing = harness.request("tools/list", {}, request_id=2)
        call = harness.request("tools/call", {"name": "shared_work", "arguments": {"action": "read"}}, request_id=3)
    finally:
        harness.close()

    assert "shared_work" not in {t["name"] for t in listing["result"]["tools"]}
    assert call["result"]["isError"] is True
    assert "任务扩展未开启" in call["result"]["content"][0]["text"]


def test_memory_description_examples_match_flat_schema(temp_settings, seeded_mission):
    # The description used to show data:{action:'write',...}, which the flat schema rejects.
    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            request_id=1,
        )
        harness.notify("notifications/initialized")
        tools = harness.request("tools/list", {}, request_id=2)["result"]["tools"]
        written = harness.request(
            "tools/call",
            {"name": "memory", "arguments": {
                "action": "write", "title": "约定", "body": "发布前跑检查。", "tags": ["流程"]}},
            request_id=3,
        )
        listing = harness.request(
            "tools/call", {"name": "memory", "arguments": {"action": "read"}}, request_id=4,
        )
    finally:
        harness.close()

    descriptor = next(t for t in tools if t["name"] == "memory")
    schema = descriptor["inputSchema"]
    properties = set(schema["properties"])
    assert "data:" not in descriptor["description"]
    examples = re.findall(r"\{([^{}]*)\}", descriptor["description"])
    assert {"read", "write", "revise", "forget"} <= {
        part.split(":", 1)[1].strip("'") for example in examples
        for part in example.split(",") if part.startswith("action:")}
    for example in examples:
        fields = {part.split(":", 1)[0].strip().rstrip("?") for part in example.split(",")}
        assert fields <= properties, example
    assert written["result"].get("isError") is not True, written
    entries = json.loads(listing["result"]["content"][0]["text"])["entries"]
    assert [entry["title"] for entry in entries] == ["约定"]


def test_read_context_returns_messages_and_mission(temp_settings, seeded_mission, repository):
    # Seed a message so read_context has something to return.
    from backend.app.domain.models import Message, MessageRole
    msg = Message(
        id="msg_ctx_test",
        mission_id=seeded_mission.id,
        conversation_id=seeded_mission.conversation_id,
        role=MessageRole.USER,
        content="测试用户消息",
    )
    repository.add_message(msg)
    legacy_id = repository.add_event(seeded_mission.id, "interrupt", {"content": "历史插话"})
    inserted = repository.add_interruption(seeded_mission.id, "新插话")
    repository.add_event(seeded_mission.id, "cli.stdout", {"content": "不应进入对话"})

    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request(
            "tools/call",
            {"name": "read_context", "arguments": {"limit": 10}},
            request_id=2,
        )
    finally:
        harness.close()

    result = response.get("result")
    assert result is not None, response
    assert result.get("isError") is not True, result
    import json as _json
    payload = _json.loads(result["content"][0]["text"])
    assert "mission" in payload
    assert "messages" in payload
    assert isinstance(payload["messages"], list)
    assert any(m["id"] == "msg_ctx_test" for m in payload["messages"])
    assert [m["id"] for m in payload["messages"]] == ["msg_ctx_test", f"interrupt:{legacy_id}", inserted.id]
    assert [m["content"] for m in payload["messages"]] == ["测试用户消息", "历史插话", "新插话"]

    events = repository.list_events_after(seeded_mission.id, 0)
    tool_calls = [e for e in events if e.event_type == "tool.call" and e.payload.get("tool_name") == "read_context"]
    assert len(tool_calls) >= 1


def test_write_artifact_creates_file_and_records_event(temp_settings, seeded_mission, repository):
    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request(
            "tools/call",
            {"name": "write_artifact", "arguments": {"relative_path": "report.md", "content": "# 报告"}},
            request_id=2,
        )
    finally:
        harness.close()

    result = response.get("result")
    assert result is not None, response
    assert result.get("isError") is not True, result
    assert "ok" in result["content"][0]["text"]

    # Check that the artifact file was written.
    artifact_file = temp_settings.artifact_root / seeded_mission.id / "report.md"
    assert artifact_file.exists()
    assert artifact_file.read_text(encoding="utf-8") == "# 报告"

    # Check that artifact event was recorded.
    events = repository.list_events_after(seeded_mission.id, 0)
    artifact_events = [e for e in events if e.event_type == "artifact"]
    assert any(e.payload.get("path") == "report.md" for e in artifact_events)
    tool_results = [e for e in events if e.event_type == "tool.result" and e.payload.get("tool_name") == "write_artifact"]
    assert len(tool_results) >= 1


def test_write_artifact_imports_from_host_workspace_via_stdio(temp_settings, seeded_mission, repository, tmp_path):
    workspace = tmp_path / "host-workspace"
    workspace.mkdir()
    original = workspace / "demo.html"
    original.write_bytes("<h1>文件导入</h1>\r\n".encode())
    harness = MCPServerHarness(
        db_path=temp_settings.database_path, artifact_root=temp_settings.artifact_root,
        mission_id=seeded_mission.id, agent_id="wangye", turn_id="t_import", provider_id="antigravity",
        extra_env={"YIREN_MCP_WORKSPACE_ROOT": str(workspace)},
    )
    try:
        response = harness.request("tools/call", {"name": "write_artifact", "arguments": {
            "relative_path": "demo.html", "source_path": "demo.html",
        }}, request_id=1)
        assert not response["result"]["isError"]
    finally:
        harness.close()
    assert (temp_settings.artifact_root / seeded_mission.id / "demo.html").read_bytes() == original.read_bytes()


def test_read_artifact_returns_content_via_jsonrpc(temp_settings, seeded_mission, repository):
    artifact_dir = temp_settings.artifact_root / seeded_mission.id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "plan.md").write_text("alpha\nbeta\n", encoding="utf-8")

    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request(
            "tools/call",
            {"name": "read_artifact", "arguments": {"relative_path": "plan.md"}},
            request_id=2,
        )
    finally:
        harness.close()

    result = response.get("result")
    assert result is not None, response
    assert result.get("isError") is not True, result
    import json as _json
    payload = _json.loads(result["content"][0]["text"])
    assert payload["path"] == "plan.md"
    assert payload["truncated"] is False
    assert "alpha" in payload["content"]
    assert "beta" in payload["content"]


def test_read_artifact_missing_file_returns_is_error(temp_settings, seeded_mission):
    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request(
            "tools/call",
            {"name": "read_artifact", "arguments": {"relative_path": "nope.md"}},
            request_id=2,
        )
    finally:
        harness.close()

    result = response.get("result")
    assert result is not None, response
    assert result.get("isError") is True, result


def test_write_artifact_path_traversal_returns_is_error(temp_settings, seeded_mission):
    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request(
            "tools/call",
            {"name": "write_artifact", "arguments": {"relative_path": "../../escape.txt", "content": "bad"}},
            request_id=2,
        )
    finally:
        harness.close()

    result = response.get("result")
    assert result is not None, response
    assert result.get("isError") is True, result


def test_update_status_records_status_event(temp_settings, seeded_mission, repository):
    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request(
            "tools/call",
            {"name": "update_status", "arguments": {"status": "completed", "summary": "任务完成"}},
            request_id=2,
        )
    finally:
        harness.close()

    result = response.get("result")
    assert result is not None, response
    assert result.get("isError") is not True, result
    assert "ok" in result["content"][0]["text"]
    assert "completed" in result["content"][0]["text"]

    events = repository.list_events_after(seeded_mission.id, 0)
    status_events = [e for e in events if e.event_type == "status"]
    assert any(e.payload.get("status") == "completed" for e in status_events)
    tool_results = [e for e in events if e.event_type == "tool.result" and e.payload.get("tool_name") == "update_status"]
    assert len(tool_results) >= 1


def test_update_status_rejects_invalid_status(temp_settings, seeded_mission):
    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request(
            "initialize",
            {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request(
            "tools/call",
            {"name": "update_status", "arguments": {"status": "flying"}},
            request_id=2,
        )
    finally:
        harness.close()

    result = response.get("result")
    assert result is not None, response
    assert result.get("isError") is True, result


def test_log_thought_records_event_via_jsonrpc_without_turn_env(
    temp_settings,
    seeded_mission,
    repository,
):
    harness = _spawn(temp_settings, seeded_mission, turn_id=None)
    try:
        response = _handshake_and_call(
            harness,
            "log_thought",
            {
                "content": "先记思路，不发聊天。",
                "kind": "decision",
                "tags": ["stdio"],
            },
        )
    finally:
        harness.close()

    result = response.get("result")
    assert result is not None, response
    assert result.get("isError") is not True, result
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert isinstance(payload["event_id"], str)
    assert repository.list_messages(seeded_mission.id) == []

    events = repository.list_events_after(seeded_mission.id, 0)
    thought_events = [event for event in events if event.event_type == "agent.thought"]
    assert len(thought_events) == 1
    thought = thought_events[0].payload
    assert thought["agent_id"] == "baobao"
    assert thought["turn_id"] == "unknown"
    assert thought["kind"] == "decision"
    assert thought["content"] == "先记思路，不发聊天。"
    assert thought["tags"] == ["stdio"]


def test_tools_list_includes_invoke_agent(temp_settings, seeded_mission):
    harness = _spawn(temp_settings, seeded_mission, agent_id="chulan", provider_id="codex")
    try:
        harness.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request("tools/list", {}, request_id=2)
    finally:
        harness.close()

    tools = response["result"]["tools"]
    invoke = next((tool for tool in tools if tool["name"] == "invoke_agent"), None)
    assert invoke is not None, tools
    schema = invoke["inputSchema"]
    assert schema["properties"]["target_agent_id"]["type"] == "string"
    assert schema["properties"]["mode"]["enum"] == ["fire_and_forget", "wait_reply"]
    assert set(schema["required"]) == {"target_agent_id", "purpose"}


def test_invoke_agent_persists_a2a_request_without_runner_queue(temp_settings, seeded_mission):
    """Without a runner queue (subprocess path), invoke_agent persists an a2a.request
    event to the DB instead of raising an error."""
    from backend.app.storage.repository import Repository

    harness = _spawn(temp_settings, seeded_mission, agent_id="chulan", provider_id="codex")
    try:
        harness.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
            request_id=1,
        )
        harness.notify("notifications/initialized")
        response = harness.request(
            "tools/call",
            {
                "name": "invoke_agent",
                "arguments": {
                    "target_agent_id": "baobao",
                    "purpose": "核对异闻物档案",
                },
            },
            request_id=2,
        )
    finally:
        harness.close()

    result = response["result"]
    assert result.get("isError") is not True, "invoke_agent should succeed, got: {r}".format(r=result)
    assert "queued_position" in result["content"][0]["text"]

    # Verify a2a.request event was written to the DB
    repo = Repository(temp_settings.database_path)
    events = repo.list_events_after(seeded_mission.id, 0)
    a2a_request_events = [e for e in events if e.event_type == "a2a.request"]
    assert len(a2a_request_events) == 1
    assert a2a_request_events[0].payload["target_agent_id"] == "baobao"


def _handshake_and_call(harness, tool_name, arguments, *, request_id=2):
    harness.request(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.0.1"},
        },
        request_id=1,
    )
    harness.notify("notifications/initialized")
    return harness.request(
        "tools/call",
        {"name": tool_name, "arguments": arguments},
        request_id=request_id,
    )


def test_invoke_agent_rejects_unknown_target_when_valid_agents_set(temp_settings, seeded_mission):
    """When YIREN_MCP_VALID_AGENTS is set, invoke_agent with an unknown
    target must return isError=True with 'unknown agent' in the message."""
    harness = _spawn(
        temp_settings,
        seeded_mission,
        agent_id="chulan",
        provider_id="codex",
        extra_env={"YIREN_MCP_VALID_AGENTS": "chulan,baobao,wangye"},
    )
    try:
        response = _handshake_and_call(
            harness,
            "invoke_agent",
            {"target_agent_id": "rogue_bot", "purpose": "非法访问"},
        )
    finally:
        harness.close()

    result = response["result"]
    assert result.get("isError") is True, "expected isError for unknown target, got: {r}".format(r=result)
    assert "unknown agent" in result["content"][0]["text"]

    # No a2a.request event should have been written
    from backend.app.storage.repository import Repository
    repo = Repository(temp_settings.database_path)
    events = repo.list_events_after(seeded_mission.id, 0)
    assert not any(e.event_type == "a2a.request" for e in events)


def test_invoke_agent_enforces_budget_in_subprocess_path(temp_settings, seeded_mission):
    """When YIREN_MCP_MAX_A2A_INVOCATIONS=1, a second invoke_agent call in the
    same turn must return isError=True with 'a2a 深度耗尽'."""
    harness = _spawn(
        temp_settings,
        seeded_mission,
        agent_id="chulan",
        turn_id="t_budget",
        provider_id="codex",
        extra_env={
            "YIREN_MCP_VALID_AGENTS": "chulan,baobao,wangye",
            "YIREN_MCP_MAX_A2A_INVOCATIONS": "1",
        },
    )
    try:
        harness.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.0.1"},
            },
            request_id=1,
        )
        harness.notify("notifications/initialized")
        # First call should succeed
        resp1 = harness.request(
            "tools/call",
            {
                "name": "invoke_agent",
                "arguments": {"target_agent_id": "baobao", "purpose": "第一次"},
            },
            request_id=2,
        )
        # Second call should be rejected
        resp2 = harness.request(
            "tools/call",
            {
                "name": "invoke_agent",
                "arguments": {"target_agent_id": "wangye", "purpose": "第二次"},
            },
            request_id=3,
        )
    finally:
        harness.close()

    assert resp1["result"].get("isError") is not True, "first call should succeed"
    assert resp2["result"].get("isError") is True, "second call should fail"
    assert "a2a 深度耗尽" in resp2["result"]["content"][0]["text"]

    from backend.app.storage.repository import Repository
    repo = Repository(temp_settings.database_path)
    events = repo.list_events_after(seeded_mission.id, 0)
    a2a_requests = [e for e in events if e.event_type == "a2a.request"]
    assert len(a2a_requests) == 1, "only first invoke should be persisted"


def test_read_previous_mission_artifact_via_stdio(temp_settings, seeded_mission, repository):
    source = Mission(id="m_previous", title="spec", goal="spec", conversation_id=seeded_mission.conversation_id)
    repository.create_mission(source)
    directory = temp_settings.artifact_root / source.id
    directory.mkdir(parents=True)
    (directory / "spec.md").write_text("previous mission specification", encoding="utf-8")
    harness = _spawn(temp_settings, seeded_mission)
    try:
        harness.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
        harness.notify("notifications/initialized")
        response = harness.request("tools/call", {"name": "read_artifact", "arguments": {
            "relative_path": "spec.md", "source_mission_id": source.id,
        }}, request_id=2)
    finally:
        harness.close()
    result = response["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["mission_id"] == source.id
    assert "previous mission specification" in payload["content"]
    events = repository.list_events_after(seeded_mission.id, 0)
    assert any(e.event_type == "tool.result" and e.payload.get("tool_name") == "read_artifact" for e in events)
