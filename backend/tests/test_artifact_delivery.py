"""Existing-file delivery and saved notices survive an unpublished/timed-out turn."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api.mission_detail import build_mission_detail
from backend.app.domain.models import Conversation, Mission, MissionStatus
from backend.app.main import create_app
from backend.app.mcp import server
from backend.app.mcp.session_server import dispatch
from backend.app.mcp.tools import MCPToolError, MCPTools
from backend.app.storage.execution import ExecutionLedger


def setup_delivery(repository, temp_settings, tmp_path):
    repository.create_conversation(Conversation(id="c_delivery", title="delivery"))
    repository.create_mission(Mission(id="m_delivery", title="demo", goal="demo",
                                    conversation_id="c_delivery", status=MissionStatus.RUNNING))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tools = MCPTools(repository, temp_settings.artifact_root, workspace_root=workspace)
    return tools, workspace


def test_path_import_preserves_bytes_and_saves_without_a_message(repository, temp_settings, tmp_path):
    tools, workspace = setup_delivery(repository, temp_settings, tmp_path)
    data = "<!doctype html>\r\n<h1>演示</h1>\r\n".encode()
    (workspace / "demo.html").write_bytes(data)
    ctx = server._Context("m_delivery", "wangye", "t1", "antigravity", tools)
    answer = server._handle_tools_call(ctx, {"name": "write_artifact", "arguments": {
        "relative_path": "交付/demo.html", "source_path": "demo.html",
    }})
    assert not answer["isError"]
    target = temp_settings.artifact_root / "m_delivery/交付/demo.html"
    assert target.read_bytes() == data
    assert repository.list_messages("m_delivery") == []
    detail = build_mission_detail(repository, temp_settings, "m_delivery")
    assert detail["mission"]["status"] == "running"
    card = detail["artifact_events"][0]
    assert card["event_type"] == "artifact.saved"
    assert card["role"] == "system"
    assert card["artifact"]["path"] == "交付/demo.html"


@pytest.mark.parametrize("case", ["outside", "symlink", "directory", "oversize", "missing", "destination"])
def test_invalid_import_creates_no_saved_notice(repository, temp_settings, tmp_path, case):
    tools, workspace = setup_delivery(repository, temp_settings, tmp_path)
    source = workspace / "demo.txt"
    source.write_text("demo")
    destination = "demo.txt"
    if case in {"outside", "symlink"}:
        outside = tmp_path / "private.txt"
        outside.write_text("private")
        if case == "symlink":
            source.unlink()
            source.symlink_to(outside)
        else:
            source = outside
    elif case == "directory":
        source = workspace
    elif case == "oversize":
        with source.open("wb") as handle:
            handle.truncate(tools.IMPORT_MAX_BYTES + 1)
    elif case == "missing":
        source.unlink()
    elif case == "destination":
        destination = "../escape.txt"
    with pytest.raises(MCPToolError):
        tools.write_artifact("m_delivery", "wangye", "t1", destination, source_path=str(source))
    assert build_mission_detail(repository, temp_settings, "m_delivery")["artifact_events"] == []
    assert any(e.event_type == "tool.error" for e in repository.list_events_after("m_delivery", 0))


@pytest.mark.parametrize("arguments", [{}, {"content": "x", "source_path": "demo.txt"},
                                       {"content": None}, {"source_path": ""}])
def test_protocol_requires_one_valid_source(repository, temp_settings, tmp_path, arguments):
    tools, _ = setup_delivery(repository, temp_settings, tmp_path)
    ctx = server._Context("m_delivery", "wangye", "t1", "antigravity", tools)
    result = server._handle_tools_call(ctx, {"name": "write_artifact", "arguments": {
        "relative_path": "demo.txt", **arguments,
    }})
    assert result["isError"]


@pytest.mark.parametrize("same_conversation", [True, False])
def test_import_artifact_keeps_conversation_boundary(repository, temp_settings, tmp_path, same_conversation):
    tools, _ = setup_delivery(repository, temp_settings, tmp_path)
    repository.create_mission(Mission(id="m_source", title="source", goal="source",
                                    conversation_id="c_delivery" if same_conversation else None))
    original = tools.write_artifact("m_source", "wangye", "t0", "demo.txt", "demo")
    if same_conversation:
        imported = tools.write_artifact("m_delivery", "wangye", "t1", "demo.txt", source_path=str(original))
        assert imported.read_bytes() == original.read_bytes()
    else:
        with pytest.raises(MCPToolError, match="conversation"):
            tools.write_artifact("m_delivery", "wangye", "t1", "demo.txt", source_path=str(original))


def test_saved_card_survives_timeout_followup_refresh_and_download(repository, temp_settings, tmp_path):
    tools, workspace = setup_delivery(repository, temp_settings, tmp_path)
    (workspace / "demo.html").write_text("<h1>demo</h1>")
    tools.write_artifact("m_delivery", "wangye", "t1", "demo.html", source_path="demo.html")
    # Re-saving the same path updates one card; an unregistered file is not announced.
    tools.write_artifact("m_delivery", "wangye", "t1", "demo.html", content="<h1>latest</h1>")
    (temp_settings.artifact_root / "m_delivery/partial.txt").write_text("unfinished")
    mission = repository.get_mission("m_delivery")
    repository.update_mission(mission.model_copy(update={"status": MissionStatus.BLOCKED}))
    repository.add_event(mission.id, "turn.blocked", {"turn_id": "t1", "agent_id": "wangye", "error": "timeout"})
    repository.create_mission(Mission(id="m_next", title="next", goal="next", conversation_id="c_delivery",
                                    status=MissionStatus.COMPLETED))
    with TestClient(create_app(temp_settings)) as client:
        for _ in range(2):
            detail = client.get("/api/conversations/c_delivery").json()
            assert detail["latest_mission_detail"]["mission"]["id"] == "m_next"
            assert len(detail["artifact_events"]) == 1
            assert detail["artifact_events"][0]["artifact"]["mission_id"] == "m_delivery"
            assert detail["messages"] == []
            assert detail["failure_events"]
            assert detail["failure_events"][0]["created_at"] >= detail["artifact_events"][0]["created_at"]
        stream = client.get("/api/missions/m_delivery/stream").text
        assert "artifact.saved" in stream
        response = client.get("/api/missions/m_delivery/artifacts/file", params={"path": "demo.html"})
        assert response.content == b"<h1>latest</h1>"
        assert "attachment" in response.headers["content-disposition"]
        for path, expected in [("../../yirenhub.sqlite3", 400), ("missing.txt", 404)]:
            assert client.get("/api/missions/m_delivery/artifacts/file", params={"path": path}).status_code == expected
        assert client.get("/api/missions/missing/artifacts/file", params={"path": "demo.html"}).status_code == 404


def test_persistent_transport_import_is_idempotent(repository, temp_settings, tmp_path, monkeypatch):
    _, workspace = setup_delivery(repository, temp_settings, tmp_path)
    (workspace / "demo.bin").write_bytes(b"\x00\xff\r\n")
    monkeypatch.setenv("YIREN_MCP_WORKSPACE_ROOT", str(workspace))
    ledger = ExecutionLedger(repository)
    ledger.register("w")
    token, _ = ledger.begin("w", "m_delivery", "wangye", "t1", "antigravity", timeout=30)
    frame = {"id": 1, "method": "tools/call", "params": {"name": "write_artifact", "arguments": {
        "context_token": token, "operation_id": "demo", "relative_path": "demo.bin", "source_path": "demo.bin",
    }}}
    first = dispatch(repository, temp_settings.artifact_root, "w", frame)
    assert not first["result"]["isError"]
    assert dispatch(repository, temp_settings.artifact_root, "w", frame) == first
    assert (temp_settings.artifact_root / "m_delivery/demo.bin").read_bytes() == b"\x00\xff\r\n"
    assert len(build_mission_detail(repository, temp_settings, "m_delivery")["artifact_events"]) == 1


def test_detail_omits_atomic_write_staging_files(repository, temp_settings, tmp_path):
    setup_delivery(repository, temp_settings, tmp_path)
    folder = temp_settings.artifact_root / "m_delivery"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("ready.md", ".notes", "report.tmp", ".ready.md." + "a" * 32 + ".tmp"):
        (folder / name).write_text("content")
    detail = build_mission_detail(repository, temp_settings, "m_delivery")
    assert {a["path"] for a in detail["artifacts"]} == {"ready.md", ".notes", "report.tmp"}


def test_detail_survives_artifact_removed_after_file_check(repository, temp_settings, tmp_path, monkeypatch):
    setup_delivery(repository, temp_settings, tmp_path)
    folder = temp_settings.artifact_root / "m_delivery"
    folder.mkdir(parents=True, exist_ok=True)
    transient = folder / "transient.md"
    transient.write_text("being replaced")
    (folder / "ready.md").write_text("ready")
    original = Path.is_file

    def removed_after_check(path):
        result = original(path)
        if path == transient and result:
            path.unlink()
        return result

    # Reproduce a writer replacing a path between its type check and metadata read.
    monkeypatch.setattr(Path, "is_file", removed_after_check)
    detail = build_mission_detail(repository, temp_settings, "m_delivery")
    assert "ready.md" in {a["path"] for a in detail["artifacts"]}
