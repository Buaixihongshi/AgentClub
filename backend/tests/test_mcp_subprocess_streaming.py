"""MCPSubprocessAdapter streams partial_text into LiveProgressRegistry."""
import sys
import json
import textwrap
import threading
import time
from pathlib import Path

import pytest

from backend.app.adapters.mcp_subprocess import MCPSubprocessAdapter
from backend.app.domain.models import Conversation, Mission
from backend.app.mcp.tools import MCPTools
from backend.app.orchestration.live_progress import LiveProgressRegistry


def test_real_tool_returns_extend_a_running_subprocess_past_its_original_deadline(repository, temp_settings, seeded_mission, tmp_path):
    from backend.app.domain.models import TurnStatus
    fake_mcp = Path(__file__).with_name('_fake_mcp_cli.py')
    script = tmp_path / 'long_running_tools.py'
    script.write_text(
        'import time,json,os,runpy\n'
        'for i in range(6):\n'
        ' print(json.dumps({"type":"user","message":{"content":[{"type":"tool_result",'
        '"tool_use_id":str(i),"content":"test returned"}]}}),flush=True)\n'
        ' time.sleep(0.25)\n'
        'os.environ["YIREN_FAKE_MCP_MODE"]="publish"\n'
        f'runpy.run_path({str(fake_mcp)!r},run_name="__main__")\n'
    )
    adapter = MCPSubprocessAdapter(sys.executable, [str(script)], db_path=temp_settings.database_path,
        artifact_root=temp_settings.artifact_root, timeout=1.2, progress_check_interval=0.2)
    before = time.monotonic()
    outcome = adapter.run_turn(seeded_mission.id, 'baobao', 't_extended', 'claude', 'test',
                               MCPTools(repository, temp_settings.artifact_root))
    assert time.monotonic() - before > 1.2
    assert outcome.status == TurnStatus.COMPLETED
    events = repository.list_events_after(seeded_mission.id, 0)
    extensions = [e for e in events if e.event_type == 'turn.deadline_extended']
    assert extensions and extensions[-1].payload['extension'] >= 2
    records = list((temp_settings.database_path.parent / 'cli-diagnostics').glob('*.json'))
    diagnostic = json.loads(records[0].read_text())
    assert diagnostic['deadline_extensions'] >= 2
    assert not diagnostic['hard_timeout']


def test_text_generation_without_tool_returns_does_not_extend_the_deadline(repository, temp_settings, seeded_mission, tmp_path):
    from backend.app.domain.models import TurnStatus
    script = tmp_path / 'only_text.py'
    script.write_text(
        'import time,json\n'
        'for i in range(100):\n'
        ' print(json.dumps({"type":"stream_event","event":{"type":"content_block_delta",'
        '"delta":{"type":"text_delta","text":"working"}}}),flush=True)\n'
        ' time.sleep(0.05)\n'
    )
    adapter = MCPSubprocessAdapter(sys.executable, [str(script)], db_path=temp_settings.database_path,
        artifact_root=temp_settings.artifact_root, timeout=0.4, progress_check_interval=0.1)
    outcome = adapter.run_turn(seeded_mission.id, 'baobao', 't_no_work', 'claude', 'test',
                               MCPTools(repository, temp_settings.artifact_root))
    assert outcome.status == TurnStatus.BLOCKED
    assert not any(e.event_type == 'turn.deadline_extended' for e in repository.list_events_after(seeded_mission.id, 0))


def test_native_activity_is_persisted_and_visible_before_cli_exits(repository, temp_settings, seeded_mission, tmp_path):
    from backend.app.api.mission_detail import build_mission_detail
    from backend.app.domain.models import TurnStatus

    records = [
        {"event": "init", "conversation_id": "native_probe"},
        {"event": "step_update", "step_update": {
            "step_index": 2, "step_type": "tool", "state": "ACTIVE", "tool_name": "view_file",
            "tool_info": {"parameters": {"AbsolutePath": "/private/project/example.ts"}, "output": "hidden output"},
        }},
        {"event": "step_update", "step_update": {
            "step_index": 2, "step_type": "tool", "state": "DONE", "tool_name": "view_file",
            "tool_info": {"parameters": {"AbsolutePath": "/private/project/example.ts"}},
        }},
    ]
    script = tmp_path / "native_cli.py"
    script.write_text("import time\n" + "\n".join(
        f"print({json.dumps(record)!r}, flush=True)\ntime.sleep(0.15)" for record in records
    ))
    adapter = MCPSubprocessAdapter(command=sys.executable, args=[str(script)],
        db_path=temp_settings.database_path, artifact_root=temp_settings.artifact_root, timeout=10)
    result = []
    thread = threading.Thread(target=lambda: result.append(adapter.run_turn(
        seeded_mission.id, "wangye", "t_native", "antigravity", "probe", MCPTools(repository, temp_settings.artifact_root))))
    thread.start()
    observed = []
    try:
        for _ in range(100):
            observed = build_mission_detail(repository, temp_settings, seeded_mission.id)["tool_events"]
            if observed:
                assert thread.is_alive()
                break
            time.sleep(0.01)
        assert observed and observed[0]["event_type"] == "provider.activity"
        assert observed[0]["summary"] == "读取文件 · example.ts"
    finally:
        thread.join(timeout=10)
    assert not thread.is_alive()
    # Native progress is never promoted into a formal chat reply.
    assert result[0].status == TurnStatus.BLOCKED
    assert repository.list_messages(seeded_mission.id) == []
    persisted = build_mission_detail(repository, temp_settings, seeded_mission.id)["tool_events"]
    assert [item["phase"] for item in persisted] == ["started", "completed"]
    # The new contract records the structured detail the CLI reported; the
    # summary stays human-readable and the args/output are masked at write time.
    assert persisted[0]["args"] == {"AbsolutePath": "/private/project/example.ts"}
    assert persisted[0]["output"] == "hidden output"
    assert persisted[1]["duration_ms"] >= 0


def _make_fake_gemini_script(tmp: Path, sleep_s: float = 0.05) -> Path:
    script = tmp / "fake_gemini.py"
    script.write_text(textwrap.dedent(f'''
        import json, sys, time
        sys.stdout.write(json.dumps({{"type":"init","session_id":"s_test"}}) + "\\n")
        sys.stdout.flush()
        for chunk in ["你好", "世界", "末尾"]:
            sys.stdout.write(json.dumps({{"type":"message","role":"assistant","content":chunk}}) + "\\n")
            sys.stdout.flush()
            time.sleep({sleep_s})
        sys.stdout.write(json.dumps({{"type":"result","status":"success"}}) + "\\n")
        sys.stdout.flush()
    '''))
    return script


def _make_fake_claude_mcp_script(tmp: Path, sleep_s: float = 0.05) -> Path:
    script = tmp / "fake_claude_mcp.py"
    script.write_text(textwrap.dedent(f'''
        import json, sys, time
        def emit(payload):
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\\n")
            sys.stdout.flush()
        emit({{"type":"system","subtype":"init","session_id":"s_claude"}})
        emit({{
            "type":"stream_event",
            "event": {{
                "type":"content_block_start",
                "index": 1,
                "content_block": {{"type":"tool_use","name":"mcp__yiren_hub__publish_message","input":{{}}}},
            }},
        }})
        for chunk in ['{{"content": "你好', '，流式', '片段"}}']:
            emit({{
                "type":"stream_event",
                "event": {{
                    "type":"content_block_delta",
                    "index": 1,
                    "delta": {{"type":"input_json_delta","partial_json": chunk}},
                }},
            }})
            time.sleep({sleep_s})
        emit({{
            "type":"stream_event",
            "event": {{"type":"content_block_stop","index": 1}},
        }})
        emit({{"type":"result","subtype":"success","is_error":False}})
    '''))
    return script


@pytest.fixture()
def seeded_mission(repository):
    conv = Conversation(id="c_stream", title="t")
    repository.create_conversation(conv)
    mission = Mission(id="m_stream", title="P", goal="g", conversation_id=conv.id)
    repository.create_mission(mission)
    return mission


def test_adapter_streams_partial_text_during_run(repository, temp_settings, seeded_mission, tmp_path):
    tools = MCPTools(repository, temp_settings.artifact_root)
    registry = LiveProgressRegistry()

    fake_cli = _make_fake_gemini_script(tmp_path, sleep_s=0.05)
    adapter = MCPSubprocessAdapter(
        command=sys.executable,
        args=[str(fake_cli)],
        db_path=temp_settings.database_path,
        artifact_root=temp_settings.artifact_root,
        timeout=10,
        live_progress=registry,
    )

    seen_partial: list[str] = []
    stop_probe = threading.Event()

    def _probe():
        while not stop_probe.is_set():
            txt = registry.get_partial_text(seeded_mission.id, "t_stream")
            if txt:
                seen_partial.append(txt)
            time.sleep(0.01)

    probe = threading.Thread(target=_probe, daemon=True)
    probe.start()
    try:
        adapter.run_turn(seeded_mission.id, "wangye", "t_stream", "gemini", "请发声", tools)
    finally:
        stop_probe.set()
        probe.join(timeout=1)

    # Mid-flight registry contained growing text containing 你好 (first chunk).
    assert any("你好" in s for s in seen_partial), f"never saw 你好 mid-flight: {seen_partial[:5]}"
    # After turn finishes, registry is cleared.
    assert registry.get_partial_text(seeded_mission.id, "t_stream") == ""


def test_adapter_streams_claude_publish_message_tool_input_during_run(repository, temp_settings, seeded_mission, tmp_path):
    tools = MCPTools(repository, temp_settings.artifact_root)
    registry = LiveProgressRegistry()

    fake_cli = _make_fake_claude_mcp_script(tmp_path, sleep_s=0.05)
    adapter = MCPSubprocessAdapter(
        command=sys.executable,
        args=[str(fake_cli)],
        db_path=temp_settings.database_path,
        artifact_root=temp_settings.artifact_root,
        timeout=10,
        live_progress=registry,
    )

    seen_partial: list[str] = []
    stop_probe = threading.Event()

    def _probe():
        while not stop_probe.is_set():
            txt = registry.get_partial_text(seeded_mission.id, "t_claude")
            if txt:
                seen_partial.append(txt)
            time.sleep(0.01)

    probe = threading.Thread(target=_probe, daemon=True)
    probe.start()
    try:
        adapter.run_turn(seeded_mission.id, "chulan", "t_claude", "claude", "请发声", tools)
    finally:
        stop_probe.set()
        probe.join(timeout=1)

    assert any("你好" in s for s in seen_partial), f"never saw Claude tool input mid-flight: {seen_partial[:5]}"
    assert any("你好，流式" in s for s in seen_partial), f"never saw growing Claude tool input: {seen_partial[:5]}"
    assert registry.get_partial_text(seeded_mission.id, "t_claude") == ""


def test_adapter_clears_registry_on_blocked_path(repository, temp_settings, seeded_mission, tmp_path):
    """Even when the turn ends BLOCKED (no MCP publish), partial text must be cleared."""
    tools = MCPTools(repository, temp_settings.artifact_root)
    registry = LiveProgressRegistry()
    fake_cli = _make_fake_gemini_script(tmp_path, sleep_s=0.0)
    adapter = MCPSubprocessAdapter(
        command=sys.executable,
        args=[str(fake_cli)],
        db_path=temp_settings.database_path,
        artifact_root=temp_settings.artifact_root,
        timeout=10,
        live_progress=registry,
    )
    adapter.run_turn(seeded_mission.id, "wangye", "t_blocked", "gemini", "请发声", tools)
    assert registry.get_partial_text(seeded_mission.id, "t_blocked") == ""
