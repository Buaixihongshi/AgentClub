import importlib
import json
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.adapters.fake import FakeCLIAdapter
from backend.app.adapters.provider_router import ProviderRouterAdapter
from backend.app.api.missions import _build_mission_adapter
from backend.app.main import create_app
from backend.app.storage.repository import Repository


def make_client(settings):
    from backend.app.main import create_app

    return TestClient(create_app(settings))


def wait_for_terminal_mission(client, mission_id):
    for _ in range(50):
        response = client.get(f"/api/missions/{mission_id}")
        response.raise_for_status()
        detail = response.json()
        if detail["mission"]["status"] in {"completed", "failed", "blocked"}:
            return detail
        time.sleep(0.02)
    raise AssertionError("Mission did not reach terminal status")


def test_create_mission_runs_fake_flow(temp_settings):
    client = make_client(temp_settings)

    response = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现系统，包含后端、前端、测试和 MCP"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["mission"]["status"] in {"queued", "running"}
    assert data["mission"]["id"].startswith("m_")
    assert {"mission", "messages", "events", "artifacts"}.issubset(set(data))


def test_create_mission_accepts_target_agent_ids_for_multi_target_discussion(temp_settings):
    """Regression: `@A @B` UI submissions arrive as target_agent_ids=[A,B];
    backend must accept the list and persist it on Mission."""
    client = make_client(temp_settings)

    response = client.post(
        "/api/missions",
        json={
            "goal": "你俩讨论下接下来该做 M4 还是 Gemini",
            "target_agent_ids": ["chulan", "baobao"],
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["mission"]["target_agent_ids"] == ["chulan", "baobao"]
    assert data["mission"]["target_agent_id"] == "chulan"


def test_create_mission_rejects_unknown_target_agent_ids_member(temp_settings):
    client = make_client(temp_settings)

    response = client.post(
        "/api/missions",
        json={"goal": "讨论", "target_agent_ids": ["chulan", "luyi"]},
    )

    assert response.status_code == 400
    assert "luyi" in response.json()["detail"]


def test_create_mission_accepts_goal_without_title_and_returns_detail(temp_settings):
    client = make_client(temp_settings)

    response = client.post(
        "/api/missions",
        json={"goal": "设计一个多 Agent 联调任务"},
    )

    assert response.status_code == 201
    data = response.json()
    assert {"mission", "events", "messages", "artifacts"}.issubset(set(data))
    assert data["mission"]["goal"] == "设计一个多 Agent 联调任务"
    assert data["mission"]["title"] == "设计一个多 Agent 联调任务"
    assert data["mission"]["status"] in {"queued", "running", "completed"}


def test_create_mission_returns_before_runner_completes(temp_settings):
    client = make_client(temp_settings)

    response = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现系统，包含后端、前端、测试和 MCP"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["mission"]["status"] in {"queued", "running"}
    assert data["mission"]["status"] != "completed"


def test_build_mission_adapter_uses_fake_adapter_by_default(temp_settings):
    temp_settings.use_fake_cli = True

    adapter = _build_mission_adapter(temp_settings)

    assert isinstance(adapter, FakeCLIAdapter)


def test_build_mission_adapter_uses_provider_router_when_fake_cli_disabled(temp_settings):
    temp_settings.use_fake_cli = False

    adapter = _build_mission_adapter(temp_settings)

    assert isinstance(adapter, ProviderRouterAdapter)
    assert adapter.config_dir == temp_settings.config_dir


def test_get_mission_serializes_messages_as_thread_events(temp_settings):
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"},
    ).json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    response = client.get(f"/api/missions/{mission['id']}")

    assert response.status_code == 200
    payload = response.json()
    first_message = payload["messages"][0]
    assert {"event_type", "metadata", "role", "content"}.issubset(first_message)
    assert first_message["event_type"] == "message"
    assert first_message["role"] in {"user", "agent", "system"}
    assert first_message["content"]

    agent_messages = [message for message in payload["messages"] if message["role"] == "agent"]
    assert agent_messages
    for message in agent_messages:
        assert message["metadata"]["provider_label"] in {
            "Claude Code",
            "Codex CLI",
            "Antigravity CLI",
            "Fake CLI",
        }
        assert message["metadata"]["persona_label"] in {"张楚岚", "冯宝宝", "王也"}
        assert message["metadata"]["capability"] == "unknown"


def test_get_mission_events_preserve_conversation_order(temp_settings):
    client = make_client(temp_settings)
    goal = "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"
    detail = client.post(
        "/api/missions",
        json={"title": "Build", "goal": goal},
    ).json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    response = client.get(f"/api/missions/{mission['id']}")

    assert response.status_code == 200
    events = response.json()["events"]
    user_goal_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "message"
        and event["role"] == "user"
        and event["content"] == goal
    )
    first_agent_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] == "message" and event["role"] == "agent"
    )
    first_completion_index = next(
        index
        for index, event in enumerate(events)
        if event["event_type"] in {"mission_updated", "status"}
        and event["content"] in {"completed", "Mission completed"}
    )

    assert user_goal_index < first_agent_index
    assert first_agent_index < first_completion_index


def test_mission_stream_returns_sse_events(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]

    response = client.get(f"/api/missions/{mission['id']}/stream")

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: snapshot" in response.text


def test_stream_emits_snapshot_event(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]

    with client.stream("GET", f"/api/missions/{mission['id']}/stream") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        body = response.read().decode("utf-8")

    assert "event: snapshot" in body
    data_lines = [
        line.removeprefix("data: ")
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    snapshots = [json.loads(line) for line in data_lines]
    assert snapshots
    assert snapshots[0]["mission_id"] == mission["id"]
    assert snapshots[0]["mission"]["id"] == mission["id"]
    assert {"mission", "mission_id", "messages", "events", "artifacts", "turns", "tool_events", "interrupts", "a2a_invocations"}.issubset(
        snapshots[0]
    )


def test_unknown_mission_endpoints_return_404(temp_settings):
    client = make_client(temp_settings)

    detail = client.get("/api/missions/m_missing")
    stream = client.get("/api/missions/m_missing/stream")
    artifacts = client.get("/api/missions/m_missing/artifacts")
    interruption = client.post(
        "/api/missions/m_missing/interruptions",
        json={"content": "stop"},
    )

    assert detail.status_code == 404
    assert stream.status_code == 404
    assert artifacts.status_code == 404
    assert interruption.status_code == 404


def test_artifact_endpoint_returns_relative_paths(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]
    mission_dir = temp_settings.artifact_root / mission["id"]
    nested_artifact = mission_dir / "notes" / "report.md"
    nested_artifact.parent.mkdir(parents=True, exist_ok=True)
    nested_artifact.write_text("# Report\n", encoding="utf-8")

    response = client.get(f"/api/missions/{mission['id']}/artifacts")

    assert response.status_code == 200
    artifacts = response.json()
    nested_records = [artifact for artifact in artifacts if artifact["path"] == "notes/report.md"]
    assert nested_records
    assert nested_records[0]["title"] == "report.md"
    assert nested_records[0]["kind"] == "md"
    for artifact in artifacts:
        assert {"id", "mission_id", "title", "kind", "path", "created_at"}.issubset(set(artifact))
        assert artifact["mission_id"] == mission["id"]
        assert not Path(artifact["path"]).is_absolute()
        assert "/" not in artifact["title"]
        assert str(temp_settings.artifact_root) not in artifact["path"]


def test_get_mission_returns_frontend_artifact_shape(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]
    mission_dir = temp_settings.artifact_root / mission["id"]
    artifact = mission_dir / "notes" / "result.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Result\n", encoding="utf-8")

    response = client.get(f"/api/missions/{mission['id']}")

    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    matching = [item for item in artifacts if item["path"] == "notes/result.md"]
    assert matching
    assert matching[0]["mission_id"] == mission["id"]
    assert matching[0]["title"] == "result.md"
    assert not matching[0]["path"].startswith("/")


def test_artifact_content_endpoint_returns_file_content(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]
    artifact = temp_settings.artifact_root / mission["id"] / "notes" / "report.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Report\n正文内容\n", encoding="utf-8")

    response = client.get(
        f"/api/missions/{mission['id']}/artifacts/content",
        params={"path": "notes/report.md"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "notes/report.md"
    assert body["content"] == "# Report\n正文内容\n"
    assert body["kind"] == "md"
    assert body["truncated"] is False


def test_artifact_content_endpoint_rejects_path_traversal(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]
    # plant a secret one level above the mission dir
    secret = temp_settings.artifact_root / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")

    response = client.get(
        f"/api/missions/{mission['id']}/artifacts/content",
        params={"path": "../secret.txt"},
    )

    assert response.status_code == 400
    assert "top secret" not in response.text


def test_artifact_content_endpoint_404_for_missing_file(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]

    response = client.get(
        f"/api/missions/{mission['id']}/artifacts/content",
        params={"path": "nope.md"},
    )

    assert response.status_code == 404


def test_artifact_content_endpoint_404_for_missing_mission(temp_settings):
    client = make_client(temp_settings)

    response = client.get(
        "/api/missions/m_missing/artifacts/content",
        params={"path": "any.md"},
    )

    assert response.status_code == 404


def test_create_complex_mission_produces_artifact(temp_settings):
    app = create_app(temp_settings)
    client = TestClient(app)

    mission_response = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"},
    )
    assert mission_response.status_code == 201
    detail = mission_response.json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    artifacts_response = client.get(f"/api/missions/{mission['id']}/artifacts")
    assert artifacts_response.status_code == 200
    artifacts = artifacts_response.json()

    assert any(item["title"] == "demo-baobao.md" for item in artifacts)
    assert any(
        item["path"] == "demo-baobao.md" and item["title"] == "demo-baobao.md"
        for item in artifacts
    )
    for artifact in artifacts:
        assert not Path(artifact["path"]).is_absolute()


def test_interruption_records_event_payload(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]

    response = client.post(
        f"/api/missions/{mission['id']}/interruptions",
        json={"content": "暂停并等我确认", "target_agent_id": "baobao"},
    )

    assert response.status_code == 202
    data = response.json()
    assert {"mission", "messages", "events", "artifacts"}.issubset(set(data))
    assert data["mission"]["id"] == mission["id"]
    events = Repository(temp_settings.database_path).list_events_after(mission["id"], 0)
    interrupt_events = [event for event in events if event.event_type == "interrupt"]
    assert len(interrupt_events) == 1
    assert interrupt_events[0].payload == {
        "mission_id": mission["id"],
        "content": "暂停并等我确认",
        "target_agent_id": "baobao",
    }


def test_interruption_returns_detail_with_interrupt_event(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]

    response = client.post(
        f"/api/missions/{mission['id']}/interruptions",
        json={"content": "暂停并等我确认", "target_agent_id": "baobao"},
    )

    assert response.status_code == 202
    data = response.json()
    assert {"mission", "messages", "events", "artifacts"}.issubset(set(data))
    assert data["mission"]["id"] == mission["id"]
    interrupt_events = [
        event
        for event in data["events"]
        if event["event_type"] == "interrupt" and event["content"] == "暂停并等我确认"
    ]
    assert interrupt_events


def test_mission_stream_data_lines_are_json(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]

    response = client.get(f"/api/missions/{mission['id']}/stream")

    data_lines = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert data_lines
    for line in data_lines:
        assert isinstance(json.loads(line), dict)


def test_completed_mission_has_derived_turns(temp_settings):
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"},
    ).json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    response = client.get(f"/api/missions/{mission['id']}")

    assert response.status_code == 200
    payload = response.json()
    assert "turns" in payload
    turns = payload["turns"]
    assert isinstance(turns, list)
    assert len(turns) >= 1
    for turn in turns:
        assert "turn_id" in turn
        assert "agent_id" in turn
        assert "provider_id" in turn
        assert "status" in turn
        assert "attempts" in turn
        assert isinstance(turn["attempts"], list)
        assert len(turn["attempts"]) >= 1
        assert turn["status"] in {"started", "completed", "blocked", "failed"}


def test_completed_mission_turns_contain_message_ids(temp_settings):
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"},
    ).json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    response = client.get(f"/api/missions/{mission['id']}")

    payload = response.json()
    turns = payload["turns"]
    completed_turns = [t for t in turns if t["status"] == "completed"]
    assert completed_turns
    all_message_ids = []
    for turn in completed_turns:
        all_message_ids.extend(turn["message_ids"])
    assert len(all_message_ids) >= 1


def test_stream_snapshot_includes_turns(temp_settings):
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions", json={"title": "Build", "goal": "解释报错"}
    ).json()
    mission = detail["mission"]

    with client.stream("GET", f"/api/missions/{mission['id']}/stream") as response:
        body = response.read().decode("utf-8")

    data_lines = [
        line.removeprefix("data: ")
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    snapshots = [json.loads(line) for line in data_lines]
    assert snapshots
    assert "turns" in snapshots[-1]


def test_completed_mission_artifacts_have_ownership_fields(temp_settings):
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"},
    ).json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    response = client.get(f"/api/missions/{mission['id']}")

    payload = response.json()
    artifacts = payload["artifacts"]
    baobao_artifacts = [a for a in artifacts if a.get("agent_id") == "baobao"]
    assert baobao_artifacts
    for artifact in baobao_artifacts:
        assert artifact["agent_id"] == "baobao"
        assert artifact["turn_id"] is not None
        assert artifact["source_event_id"] is not None
        assert not artifact["path"].startswith("/")


def test_artifact_endpoint_includes_ownership_for_event_backed(temp_settings):
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"},
    ).json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    response = client.get(f"/api/missions/{mission['id']}/artifacts")

    artifacts = response.json()
    baobao = [a for a in artifacts if a["title"] == "demo-baobao.md"]
    assert baobao
    for artifact in baobao:
        assert not artifact["path"].startswith("/")


def test_filesystem_only_artifacts_have_null_ownership(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]
    mission_dir = temp_settings.artifact_root / mission["id"]
    manual_file = mission_dir / "manual.txt"
    manual_file.parent.mkdir(parents=True, exist_ok=True)
    manual_file.write_text("manual content", encoding="utf-8")

    response = client.get(f"/api/missions/{mission['id']}")

    artifacts = response.json()["artifacts"]
    manual = [a for a in artifacts if a["title"] == "manual.txt"]
    assert manual
    assert manual[0]["agent_id"] is None
    assert manual[0]["turn_id"] is None
    assert manual[0]["source_event_id"] is None


def test_completed_mission_has_tool_events(temp_settings):
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"},
    ).json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    response = client.get(f"/api/missions/{mission['id']}")

    payload = response.json()
    assert "tool_events" in payload
    tool_events = payload["tool_events"]
    assert isinstance(tool_events, list)
    assert len(tool_events) >= 1
    for te in tool_events:
        assert "id" in te
        assert "event_type" in te
        assert te["event_type"] in {"tool.call", "tool.result", "tool.error"}
        assert "tool_name" in te
        assert "agent_id" in te
        assert "turn_id" in te
        assert "created_at" in te


def test_tool_events_include_write_artifact_trace(temp_settings):
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"},
    ).json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    response = client.get(f"/api/missions/{mission['id']}")

    tool_events = response.json()["tool_events"]
    write_calls = [te for te in tool_events if te["tool_name"] == "write_artifact" and te["event_type"] == "tool.call"]
    write_results = [te for te in tool_events if te["tool_name"] == "write_artifact" and te["event_type"] == "tool.result"]
    assert write_calls
    assert write_results
    assert write_results[0].get("artifact_id")


def test_stream_snapshot_includes_tool_events(temp_settings):
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions", json={"title": "Build", "goal": "解释报错"}
    ).json()
    mission = detail["mission"]

    with client.stream("GET", f"/api/missions/{mission['id']}/stream") as response:
        body = response.read().decode("utf-8")

    data_lines = [
        line.removeprefix("data: ")
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    snapshots = [json.loads(line) for line in data_lines]
    assert snapshots
    assert "tool_events" in snapshots[-1]


def test_interruption_appears_in_derived_interrupts(temp_settings):
    # Test the derivation logic directly via repository instead of racing the
    # daemon runner thread. The runner consumes pending interrupts between
    # turns, so an end-to-end test that POSTs a mission then POSTs an
    # interrupt is inherently racy: if the runner's first interrupt check
    # fires after the POST, the interrupt is handled before the test reads.
    from backend.app.api.mission_detail import build_mission_detail
    from backend.app.domain.models import Mission, MissionStatus
    from backend.app.storage.repository import Repository

    repo = Repository(temp_settings.database_path)
    repo.initialize()
    mission = Mission(
        id="m_interrupt_test",
        title="Build",
        goal="解释报错",
        status=MissionStatus.PENDING,
    )
    repo.create_mission(mission)
    repo.add_event(
        mission.id,
        "interrupt",
        {
            "mission_id": mission.id,
            "content": "暂停并等我确认",
            "target_agent_id": "baobao",
        },
    )

    detail = build_mission_detail(repo, temp_settings, mission.id)
    interrupts = detail["interrupts"]
    assert len(interrupts) == 1
    pending = [i for i in interrupts if i["status"] == "pending"]
    assert pending
    assert pending[0]["content"] == "暂停并等我确认"
    assert pending[0]["target_agent_id"] == "baobao"
    assert pending[0]["handled_at"] is None


def test_mission_detail_includes_empty_interrupts(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission = detail["mission"]

    response = client.get(f"/api/missions/{mission['id']}")
    payload = response.json()
    assert "interrupts" in payload
    assert payload["interrupts"] == []


def test_stream_snapshot_includes_interrupts(temp_settings):
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions", json={"title": "Build", "goal": "解释报错"}
    ).json()
    mission = detail["mission"]

    with client.stream("GET", f"/api/missions/{mission['id']}/stream") as response:
        body = response.read().decode("utf-8")

    data_lines = [
        line.removeprefix("data: ")
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    snapshots = [json.loads(line) for line in data_lines]
    assert snapshots
    assert "interrupts" in snapshots[-1]


def test_single_provider_attempt_produces_one_attempt_record(temp_settings):
    """A successful turn with no fallback should have exactly 1 attempt record."""
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"},
    ).json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    response = client.get(f"/api/missions/{mission['id']}")
    payload = response.json()
    turns = payload["turns"]
    for turn in turns:
        if turn["status"] == "completed":
            for attempt in turn["attempts"]:
                if attempt["status"] == "completed":
                    assert attempt["attempt"] is not None
                    assert attempt["provider_id"]


def test_fallback_turn_has_two_attempt_records(temp_settings):
    """A turn with default provider blocked + fallback completed should have 2 attempts."""
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"},
    ).json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    response = client.get(f"/api/missions/{mission['id']}")
    payload = response.json()
    turns = payload["turns"]
    for turn in turns:
        started_events_count = sum(
            1 for a in turn["attempts"] if a.get("status") in ("started", "completed", "blocked", "failed")
        )
        assert started_events_count == len(turn["attempts"])
        attempt_keys = set()
        for a in turn["attempts"]:
            key = (a["attempt"], a["provider_id"])
            assert key not in attempt_keys, "Duplicate attempt key: {k}".format(k=key)
            attempt_keys.add(key)


def test_attempt_records_have_final_status(temp_settings):
    """Each attempt record should have a terminal status, not just 'started'."""
    client = make_client(temp_settings)
    detail = client.post(
        "/api/missions",
        json={"title": "Build", "goal": "设计并实现一个多 Agent 聊天室系统，包含后端、前端、测试和 MCP"},
    ).json()
    mission = detail["mission"]
    wait_for_terminal_mission(client, mission["id"])

    response = client.get(f"/api/missions/{mission['id']}")
    payload = response.json()
    turns = payload["turns"]
    for turn in turns:
        for attempt in turn["attempts"]:
            assert attempt["status"] in {"completed", "blocked", "failed", "started"}


def test_create_conversation_returns_201_with_c_id(temp_settings):
    client = make_client(temp_settings)
    response = client.post("/api/conversations", json={"title": "测试对话"})
    assert response.status_code == 201
    data = response.json()
    assert data["id"].startswith("c_")
    assert data["title"] == "测试对话"
    assert data["status"] == "active"


def test_list_conversations_returns_created(temp_settings):
    client = make_client(temp_settings)
    client.post("/api/conversations", json={"title": "A"})
    client.post("/api/conversations", json={"title": "B"})
    response = client.get("/api/conversations")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 2
    for item in data:
        assert "mission_count" in item


def test_create_conversation_mission_starts_mission(temp_settings):
    client = make_client(temp_settings)
    conv = client.post("/api/conversations", json={"title": "测试"}).json()
    conv_id = conv["id"]

    response = client.post(
        f"/api/conversations/{conv_id}/missions",
        json={"goal": "设计方案"},
    )
    assert response.status_code == 201
    detail = response.json()
    assert "conversation" in detail
    assert detail["conversation"]["id"] == conv_id
    assert len(detail["missions"]) >= 1
    assert detail["missions"][0]["conversation_id"] == conv_id


def test_conversation_detail_aggregates_messages_from_multiple_missions(temp_settings):
    client = make_client(temp_settings)
    conv = client.post("/api/conversations", json={"title": "多轮"}).json()
    conv_id = conv["id"]

    client.post(f"/api/conversations/{conv_id}/missions", json={"goal": "第一轮"})
    time.sleep(0.3)
    client.post(f"/api/conversations/{conv_id}/missions", json={"goal": "第二轮"})
    time.sleep(0.3)

    response = client.get(f"/api/conversations/{conv_id}")
    assert response.status_code == 200
    detail = response.json()
    assert len(detail["missions"]) == 2
    user_msgs = [m for m in detail["messages"] if m.get("role") == "user"]
    assert len(user_msgs) >= 2


def test_legacy_mission_api_auto_creates_conversation(temp_settings):
    client = make_client(temp_settings)
    response = client.post("/api/missions", json={"goal": "旧 API 测试"})
    assert response.status_code == 201
    data = response.json()
    assert data["mission"]["conversation_id"] is not None
    assert data["mission"]["conversation_id"].startswith("c_")


def test_legacy_mission_api_accepts_target_agent_id(temp_settings):
    client = make_client(temp_settings)
    response = client.post(
        "/api/missions",
        json={"goal": "指定王也", "target_agent_id": "wangye"},
    )
    assert response.status_code == 201
    assert response.json()["mission"]["target_agent_id"] == "wangye"


def test_invalid_target_agent_id_returns_400(temp_settings):
    client = make_client(temp_settings)
    response = client.post(
        "/api/missions",
        json={"goal": "无效 agent", "target_agent_id": "unknown"},
    )
    assert response.status_code == 400
    assert "Invalid target_agent_id" in response.json()["detail"]


def test_conversation_mission_invalid_target_returns_400(temp_settings):
    client = make_client(temp_settings)
    conv = client.post("/api/conversations", json={"title": "T"}).json()
    response = client.post(
        f"/api/conversations/{conv['id']}/missions",
        json={"goal": "test", "target_agent_id": "invalid"},
    )
    assert response.status_code == 400


def test_conversation_not_found_returns_404(temp_settings):
    client = make_client(temp_settings)
    response = client.get("/api/conversations/c_nonexistent")
    assert response.status_code == 404


def test_legacy_mission_with_missing_conversation_id_returns_404(temp_settings):
    client = make_client(temp_settings)
    response = client.post(
        "/api/missions",
        json={"goal": "test", "conversation_id": "c_missing"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Conversation not found"


def test_conversation_summary_preview_uses_latest_message(temp_settings):
    client = make_client(temp_settings)
    conv = client.post("/api/conversations", json={"title": "Preview"}).json()
    conv_id = conv["id"]
    client.post(
        f"/api/conversations/{conv_id}/missions",
        json={"goal": "用户刚发的消息"},
    )

    summaries = client.get("/api/conversations").json()
    target = [s for s in summaries if s["id"] == conv_id]
    assert target
    preview = target[0]["latest_message_preview"]
    assert preview is not None
    assert len(preview) > 0


def test_conversation_summary_derives_title_from_first_goal(temp_settings):
    client = make_client(temp_settings)
    # 不传 title -> 落到缺省占位 "新对话"
    conv = client.post("/api/conversations", json={}).json()
    conv_id = conv["id"]
    assert conv["title"] == "新对话"
    client.post(
        f"/api/conversations/{conv_id}/missions",
        json={"goal": "@张楚岚 帮我把左侧列表的标题做得能区分。后面还有别的"},
    )

    summaries = client.get("/api/conversations").json()
    target = next(s for s in summaries if s["id"] == conv_id)
    # 剥掉 @点名、只取头一句
    assert target["title"] == "帮我把左侧列表的标题做得能区分"


def test_conversation_summary_keeps_user_title(temp_settings):
    client = make_client(temp_settings)
    conv = client.post("/api/conversations", json={"title": "我的专题"}).json()
    conv_id = conv["id"]
    client.post(
        f"/api/conversations/{conv_id}/missions",
        json={"goal": "随便一个目标"},
    )

    summaries = client.get("/api/conversations").json()
    target = next(s for s in summaries if s["id"] == conv_id)
    assert target["title"] == "我的专题"


def test_importing_main_does_not_initialize_default_storage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("backend.app.main", None)

    importlib.import_module("backend.app.main")

    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "artifacts").exists()


def test_get_mission_derives_a2a_invocations(temp_settings):
    client = make_client(temp_settings)
    detail = client.post("/api/missions", json={"title": "Build", "goal": "解释报错"}).json()
    mission_id = detail["mission"]["id"]
    repo = Repository(temp_settings.database_path)
    repo.add_event(
        mission_id,
        "a2a.invoke",
        {
            "mission_id": mission_id,
            "sender_agent_id": "chulan",
            "sender_turn_id": "turn_1_sender",
            "target_agent_id": "baobao",
            "purpose": "核对异闻物档案",
            "mode": "fire_and_forget",
            "queued_position": 1,
        },
    )

    response = client.get(f"/api/missions/{mission_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["a2a_invocations"] == [
        {
            "event_id": payload["a2a_invocations"][0]["event_id"],
            "sender_agent_id": "chulan",
            "sender_turn_id": "turn_1_sender",
            "target_agent_id": "baobao",
            "target_turn_id": None,
            "purpose": "核对异闻物档案",
            "mode": "fire_and_forget",
            "queued_position": 1,
            "created_at": payload["a2a_invocations"][0]["created_at"],
            "reply_text": None,
            "reply_failed": False,
        }
    ]


def test_mission_detail_a2a_invocations_include_reply_text_for_wait_reply(temp_settings):
    client = make_client(temp_settings)
    response = client.post("/api/missions", json={"title": "WR", "goal": "g"})
    mission_id = response.json()["mission"]["id"]
    repo = Repository(temp_settings.database_path)

    repo.add_event(mission_id, "a2a.invoke", {
        "sender_agent_id": "chulan", "sender_turn_id": "ts1",
        "target_agent_id": "baobao", "purpose": "x",
        "mode": "wait_reply", "queued_position": 1,
    })
    repo.add_event(mission_id, "turn.started", {
        "turn_id": "tc1", "agent_id": "baobao", "provider_id": "fake",
        "attempt": 1, "purpose": "x", "a2a_sender_turn_id": "ts1",
    })
    repo.add_event(mission_id, "a2a.reply", {
        "turn_id": "tc1", "agent_id": "baobao",
        "sender_agent_id": "chulan",
        "sender_resume_step_id": "rs1",
        "reply_text": "档案里没有", "reply_message_id": "msg1",
        "truncated": False, "invoke_event_id": "1",
    })

    detail = client.get(f"/api/missions/{mission_id}").json()
    invocations = detail["a2a_invocations"]
    assert len(invocations) == 1
    assert invocations[0]["mode"] == "wait_reply"
    assert invocations[0]["reply_text"] == "档案里没有"
    assert invocations[0]["reply_failed"] is False
    assert invocations[0]["target_turn_id"] == "tc1"


def test_rename_conversation(temp_settings):
    client = make_client(temp_settings)
    conv = client.post("/api/conversations", json={"title": "Old Name"}).json()
    conv_id = conv["id"]

    response = client.patch(f"/api/conversations/{conv_id}", json={"title": "New Name"})
    assert response.status_code == 200
    data = response.json()
    assert data["conversation"]["title"] == "New Name"

    # Check summary as well
    summaries = client.get("/api/conversations").json()
    match = next(s for s in summaries if s["id"] == conv_id)
    assert match["title"] == "New Name"


def test_archive_and_restore_conversation(temp_settings):
    client = make_client(temp_settings)
    conv = client.post("/api/conversations", json={"title": "可归档会话"}).json()
    conv_id = conv["id"]

    archived = client.patch(
        f"/api/conversations/{conv_id}",
        json={"status": "archived"},
    )

    assert archived.status_code == 200
    assert archived.json()["conversation"]["status"] == "archived"
    assert all(item["id"] != conv_id for item in client.get("/api/conversations").json())
    archived_list = client.get("/api/conversations", params={"status": "archived"}).json()
    assert [item["id"] for item in archived_list] == [conv_id]
    blocked = client.post(
        f"/api/conversations/{conv_id}/missions",
        json={"goal": "归档后不应继续发送"},
    )
    assert blocked.status_code == 409

    restored = client.patch(
        f"/api/conversations/{conv_id}",
        json={"status": "active"},
    )
    assert restored.status_code == 200
    assert restored.json()["conversation"]["status"] == "active"
    assert any(item["id"] == conv_id for item in client.get("/api/conversations").json())
