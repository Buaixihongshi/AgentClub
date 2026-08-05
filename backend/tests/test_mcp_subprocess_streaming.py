"""MCPSubprocessAdapter streams partial_text into LiveProgressRegistry."""
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from backend.app.adapters.mcp_subprocess import MCPSubprocessAdapter
from backend.app.domain.models import Conversation, Mission
from backend.app.mcp.tools import MCPTools
from backend.app.orchestration.live_progress import LiveProgressRegistry


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
