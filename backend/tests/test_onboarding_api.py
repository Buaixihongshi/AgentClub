import time

from fastapi.testclient import TestClient

from backend.app.adapters.base import CLIAdapter, TurnOutcome
from backend.app.adapters.fake import FakeCLIAdapter
from backend.app.domain.models import TurnStatus
from backend.app.main import create_app


def _wait_for_onboarding(client: TestClient, expected: str) -> dict:
    for _ in range(100):
        response = client.get("/api/onboarding")
        response.raise_for_status()
        state = response.json()
        if state["state"] == expected:
            return state
        time.sleep(0.02)
    raise AssertionError("onboarding did not reach {state}".format(state=expected))


def test_health_reports_local_bootstrap_dependencies(temp_settings):
    client = TestClient(create_app(temp_settings))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "0.1.0",
        "database": "ready",
        "config": "ready",
        "fake_provider": "ready",
    }


def test_first_chat_is_idempotent_and_uses_fake_chulan(temp_settings):
    # User settings must not be able to turn the bootstrap mission into a real call.
    temp_settings.use_fake_cli = False
    client = TestClient(create_app(temp_settings))

    initial = client.get("/api/onboarding")
    first = client.post("/api/onboarding/first-chat")
    second = client.post("/api/onboarding/first-chat")

    assert initial.status_code == 200
    assert initial.json()["state"] == "ready_for_first_chat"
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["conversation"]["conversation"]["id"] == second.json()["conversation"]["conversation"]["id"]
    assert first.json()["conversation"]["latest_mission_detail"]["mission"]["id"] == second.json()["conversation"]["latest_mission_detail"]["mission"]["id"]

    state = _wait_for_onboarding(client, "first_chat_succeeded")
    detail = client.get(
        "/api/conversations/{conversation_id}".format(
            conversation_id=state["conversation_id"],
        )
    ).json()
    agent_messages = [message for message in detail["messages"] if message["role"] == "agent"]

    assert len(detail["missions"]) == 1
    assert len(agent_messages) == 1
    assert agent_messages[0]["agent_id"] == "chulan"
    assert agent_messages[0]["provider_id"] == "fake"
    assert "AgentClub 这边已经跑通了" in agent_messages[0]["content"]
    assert "聊天室模式" not in agent_messages[0]["content"]
    assert state["first_reply_completed_at"]


def test_onboarding_can_be_completed_and_reset_without_losing_other_settings(temp_settings):
    from backend.app.storage import hub_settings

    settings_path = hub_settings.default_path(temp_settings)
    hub_settings.save(
        settings_path,
        {
            "schema_version": 1,
            "providers": {"codex": {"enabled": True}},
        },
    )
    client = TestClient(create_app(temp_settings))

    completed = client.patch(
        "/api/onboarding",
        json={"completed": True, "dismissed_real_cli_setup": True},
    )
    reset = client.post("/api/onboarding/reset")

    assert completed.status_code == 200
    assert completed.json()["state"] == "completed"
    assert reset.status_code == 200
    assert reset.json()["state"] == "ready_for_first_chat"
    persisted = hub_settings.load(settings_path)
    assert persisted["providers"] == {"codex": {"enabled": True}}


def test_recommended_provider_is_verified_before_atomic_activation(temp_settings, monkeypatch):
    from backend.app.api import onboarding as onboarding_api
    from backend.app.storage import hub_settings

    monkeypatch.setattr(
        onboarding_api,
        "_provider_readiness",
        lambda settings, provider_id: {
            "available": True,
            "version": "Codex CLI 9.9.9" if provider_id == "codex" else f"{provider_id} 1.0",
            "path": f"/test/bin/{provider_id}",
            "error": None,
        },
    )
    settings_path = hub_settings.default_path(temp_settings)
    hub_settings.save(
        settings_path,
        {
            "schema_version": 1,
            "providers": {"codex": {"enabled": False}},
            "runtime": {"max_turns": 4},
        },
    )
    client = TestClient(
        create_app(
            temp_settings,
            onboarding_verification_adapter=FakeCLIAdapter(
                response="哥，Codex 已通过 MCP 接入 AgentClub。"
            ),
        )
    )

    client.post("/api/onboarding/first-chat").raise_for_status()
    _wait_for_onboarding(client, "first_chat_succeeded")

    not_confirmed = client.post(
        "/api/onboarding/providers/codex/verify",
        json={"confirmed_real_call": False},
    )
    verify = client.post(
        "/api/onboarding/providers/codex/verify",
        json={"confirmed_real_call": True},
    )

    assert not_confirmed.status_code == 400
    assert verify.status_code == 202
    before_activation = hub_settings.load(settings_path)
    assert before_activation["runtime"] == {"max_turns": 4}
    assert before_activation["providers"] == {"codex": {"enabled": False}}

    verified = _wait_for_onboarding(client, "provider_verified")
    assert verified["verified_provider_ids"] == ["codex"]
    codex = next(item for item in verified["providers"] if item["id"] == "codex")
    assert codex["verified"] is True
    assert codex["version"] == "Codex CLI 9.9.9"
    assert verified["recommended_provider_id"] == "codex"

    detail = client.get(
        "/api/conversations/{conversation_id}".format(
            conversation_id=verified["conversation_id"],
        )
    ).json()
    provider_ids = [
        message["provider_id"]
        for message in detail["messages"]
        if message["role"] == "agent"
    ]
    # Provider verification is an internal diagnostic. Its prompt and model
    # reply must never be rendered as ordinary conversation messages.
    assert provider_ids == ["fake"]

    activated = client.post("/api/onboarding/providers/codex/activate")
    assert activated.status_code == 202
    activated_body = activated.json()
    assert activated_body["onboarding"]["state"] in {
        "team_intro_running",
        "team_intro_completed",
    }
    intro_mission_id = activated_body["onboarding"]["team_intro_mission_id"]

    # Repeated activation must attach to the same introduction mission rather
    # than adding duplicate user prompts or character replies.
    repeated = client.post("/api/onboarding/providers/codex/activate")
    assert repeated.status_code == 202
    assert repeated.json()["onboarding"]["team_intro_mission_id"] == intro_mission_id

    introduced = _wait_for_onboarding(client, "team_intro_completed")
    assert introduced["activated_provider_id"] == "codex"
    assert introduced["team_intro_completed_at"]

    detail = client.get(
        "/api/conversations/{conversation_id}".format(
            conversation_id=introduced["conversation_id"],
        )
    ).json()
    agent_messages = [
        message for message in detail["messages"] if message["role"] == "agent"
    ]
    assert [message["agent_id"] for message in agent_messages] == [
        "chulan",
        "baobao",
        "wangye",
    ]
    assert [message["provider_id"] for message in agent_messages] == [
        "fake",
        "fake",
        "fake",
    ]
    assert "代码、文件" in agent_messages[1]["content"]
    assert "风险复盘" in agent_messages[2]["content"]

    completed = client.patch("/api/onboarding", json={"completed": True})
    assert completed.status_code == 200
    assert completed.json()["state"] == "completed"

    persisted = hub_settings.load(settings_path)
    assert persisted["runtime"] == {"max_turns": 4, "use_fake_cli": False}
    assert persisted["providers"] == {
        "codex": {"enabled": True},
        "fake": {"enabled": True},
    }
    assert persisted["agents"] == {
        "chulan": {
            "default_provider_id": "codex",
            "fallback_provider_ids": ["fake"],
        },
        "baobao": {
            "default_provider_id": "fake",
            "fallback_provider_ids": [],
        },
        "wangye": {
            "default_provider_id": "fake",
            "fallback_provider_ids": [],
        },
    }
    assert persisted["onboarding"]["team_intro_mission_id"] == intro_mission_id
    assert persisted["onboarding"]["team_intro_completed_at"]


def test_completed_codex_onboarding_can_verify_an_extension_provider(temp_settings, monkeypatch):
    from backend.app.api import onboarding as onboarding_api

    monkeypatch.setattr(
        onboarding_api,
        "_provider_readiness",
        lambda settings, provider_id: {
            "available": True,
            "version": f"{provider_id} 9.9.9",
            "path": f"/test/bin/{provider_id}",
            "error": None,
        },
    )
    client = TestClient(
        create_app(
            temp_settings,
            onboarding_verification_adapter=FakeCLIAdapter(
                response="已经通过 MCP 接入 AgentClub。"
            ),
        )
    )

    client.post("/api/onboarding/first-chat").raise_for_status()
    _wait_for_onboarding(client, "first_chat_succeeded")
    client.post(
        "/api/onboarding/providers/codex/verify",
        json={"confirmed_real_call": True},
    ).raise_for_status()
    _wait_for_onboarding(client, "provider_verified")
    client.post("/api/onboarding/providers/codex/activate").raise_for_status()
    _wait_for_onboarding(client, "team_intro_completed")
    client.patch("/api/onboarding", json={"completed": True}).raise_for_status()

    verify = client.post(
        "/api/onboarding/providers/claude/verify",
        json={"confirmed_real_call": True},
    )

    assert verify.status_code == 202
    assert verify.json()["onboarding"]["state"] in {
        "provider_verifying",
        "provider_verified",
    }
    extended = _wait_for_onboarding(client, "provider_verified")
    assert extended["activated_provider_id"] == "codex"
    assert extended["verification_provider_id"] == "claude"
    assert extended["verified_provider_ids"] == ["claude", "codex"]
    assert extended["completed_at"] is None

    completed = client.patch("/api/onboarding", json={"completed": True})
    assert completed.status_code == 200
    assert completed.json()["state"] == "completed"


class _BlockedVerificationAdapter(CLIAdapter):
    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        return TurnOutcome(
            status=TurnStatus.BLOCKED,
            summary="Codex authentication required",
        )


class _PublishingThenBlockedVerificationAdapter(CLIAdapter):
    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        tools.publish_message(
            mission_id,
            agent_id,
            turn_id,
            "我错误地声称已经通过 Codex CLI 接入。",
            provider_id,
        )
        return TurnOutcome(
            status=TurnStatus.BLOCKED,
            summary="Antigravity authentication required",
        )


def test_failed_provider_verification_keeps_fake_settings(temp_settings, monkeypatch):
    from backend.app.api import onboarding as onboarding_api
    from backend.app.storage import hub_settings

    monkeypatch.setattr(
        onboarding_api,
        "_provider_readiness",
        lambda settings, provider_id: {
            "available": True,
            "version": "Codex CLI 9.9.9",
            "path": "/test/bin/codex",
            "error": None,
        },
    )
    client = TestClient(
        create_app(
            temp_settings,
            onboarding_verification_adapter=_BlockedVerificationAdapter(),
        )
    )
    client.post("/api/onboarding/first-chat").raise_for_status()
    _wait_for_onboarding(client, "first_chat_succeeded")

    client.post(
        "/api/onboarding/providers/codex/verify",
        json={"confirmed_real_call": True},
    ).raise_for_status()
    failed = _wait_for_onboarding(client, "provider_verification_failed")

    assert "authentication required" in failed["verification_error"]
    persisted = hub_settings.load(hub_settings.default_path(temp_settings))
    assert persisted.get("runtime", {}).get("use_fake_cli") is not False
    assert persisted.get("providers", {}).get("codex", {}).get("enabled") is not True
    assert client.post("/api/onboarding/providers/codex/activate").status_code == 409


def test_verification_prompt_and_messages_are_isolated_from_chat(temp_settings, monkeypatch):
    from backend.app.api import onboarding as onboarding_api

    class _CapturingAdapter(CLIAdapter):
        prompt = ""

        def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
            self.prompt = prompt
            tools.publish_message(
                mission_id,
                agent_id,
                turn_id,
                "连接测试完成",
                provider_id,
            )
            return TurnOutcome(status=TurnStatus.COMPLETED)

    monkeypatch.setattr(
        onboarding_api,
        "_provider_readiness",
        lambda settings, provider_id: {
            "available": True,
            "version": "Codex CLI 9.9.9",
            "path": "/test/bin/codex",
            "error": None,
        },
    )
    adapter = _CapturingAdapter()
    client = TestClient(
        create_app(temp_settings, onboarding_verification_adapter=adapter)
    )
    client.post("/api/onboarding/first-chat").raise_for_status()
    first = _wait_for_onboarding(client, "first_chat_succeeded")

    started = client.post(
        "/api/onboarding/providers/codex/verify",
        json={"confirmed_real_call": True},
    )
    started.raise_for_status()
    _wait_for_onboarding(client, "provider_verified")

    assert "最近对话" not in adapter.prompt
    assert "接下来会认识" not in adapter.prompt
    assert "不要声明当前 Provider 名称" in adapter.prompt

    detail = client.get(
        "/api/conversations/{conversation_id}".format(
            conversation_id=first["conversation_id"],
        )
    ).json()
    assert len(detail["missions"]) == 1
    assert [m["provider_id"] for m in detail["messages"] if m["role"] == "agent"] == ["fake"]
    assert all("连接测试完成" not in m["content"] for m in detail["messages"])


def test_blocked_verification_cannot_pollute_conversation(temp_settings, monkeypatch):
    from backend.app.api import onboarding as onboarding_api

    monkeypatch.setattr(
        onboarding_api,
        "_provider_readiness",
        lambda settings, provider_id: {
            "available": True,
            "version": "Antigravity 1.1.5",
            "path": "/test/bin/agy",
            "error": None,
        },
    )
    client = TestClient(
        create_app(
            temp_settings,
            onboarding_verification_adapter=_PublishingThenBlockedVerificationAdapter(),
        )
    )
    client.post("/api/onboarding/first-chat").raise_for_status()
    first = _wait_for_onboarding(client, "first_chat_succeeded")
    client.post(
        "/api/onboarding/providers/antigravity/verify",
        json={"confirmed_real_call": True},
    ).raise_for_status()
    failed = _wait_for_onboarding(client, "provider_verification_failed")

    assert "authentication required" in failed["verification_error"]
    assert "antigravity" not in failed["verified_provider_ids"]
    detail = client.get(
        "/api/conversations/{conversation_id}".format(
            conversation_id=first["conversation_id"],
        )
    ).json()
    assert len(detail["missions"]) == 1
    assert all("错误地声称" not in message["content"] for message in detail["messages"])
