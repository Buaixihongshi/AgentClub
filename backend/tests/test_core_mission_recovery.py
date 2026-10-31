"""A restart must not leave an ordinary chat mission running and its conversation locked."""
import os
import subprocess
import sys
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app import extension_points, extensions
from backend.app.adapters.fake import FakeCLIAdapter
from backend.app.api.mission_detail import build_mission_detail
from backend.app.domain.models import Conversation, Mission, MissionStatus, RoomMode, utc_now
from backend.app.main import create_app
from backend.app.orchestration import interruption
from backend.app.orchestration.runner import MissionRunner


class _ProcessKilled(BaseException):
    """Stands in for the backend process dying mid-turn: nothing after it runs."""


class _DiesOnSecondTurn(FakeCLIAdapter):
    def __init__(self):
        super().__init__(response="先说一半")
        self.turns = 0

    def run_turn(self, *args, **kwargs):
        self.turns += 1
        if self.turns == 2:
            raise _ProcessKilled()
        return super().run_turn(*args, **kwargs)


def _seed(repository, mission_id="m_restart", **fields):
    repository.create_conversation(Conversation(id="c_" + mission_id, title="restart"))
    mission = Mission(id=mission_id, title="t", goal="一起看看", conversation_id="c_" + mission_id, **fields)
    repository.create_mission(mission)
    return mission


def _dead_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def _owned_by(repository, mission, pid, instance="elsewhere"):
    repository.add_event(mission.id, interruption.OWNER_EVENT, {"pid": pid, "instance": instance})


def test_restart_frees_conversation_and_names_who_never_spoke(repository, temp_settings, monkeypatch):
    mission = _seed(repository, target_agent_ids=["baobao", "chulan", "wangye"])
    with pytest.raises(_ProcessKilled):
        MissionRunner(repository, temp_settings, _DiesOnSecondTurn()).run(mission.id)
    assert repository.get_mission(mission.id).status == MissionStatus.RUNNING
    # The restarted backend is a new process, even if it got the same pid back.
    monkeypatch.setattr(interruption, "INSTANCE_ID", "restarted")

    with TestClient(create_app(temp_settings)) as client:
        assert repository.get_mission(mission.id).status == MissionStatus.BLOCKED
        detail = build_mission_detail(repository, temp_settings, mission.id)
        assert [m["content"] for m in detail["messages"] if m["role"] == "agent"] == ["先说一半"]
        assert [f["content"] for f in detail["failure_events"]] == [
            "张楚岚本轮因服务重启中断，未发布回复。原计划尚未发言：王也。可以直接发消息继续。"
        ]
        assert detail["turns"][-1]["status"] == "blocked"

        reply = client.post("/api/missions", json={"goal": "继续", "conversation_id": mission.conversation_id})
        assert reply.status_code == 201


def test_restart_closes_mission_that_never_started_a_turn(repository, temp_settings):
    mission = _seed(repository, status=MissionStatus.PENDING, updated_at=utc_now() - timedelta(minutes=5))
    with TestClient(create_app(temp_settings)):
        detail = build_mission_detail(repository, temp_settings, mission.id)
    assert detail["mission"]["status"] == "blocked"
    assert [f["content"] for f in detail["failure_events"]] == ["本次任务因服务重启中断。可以直接发消息继续。"]


def test_restart_leaves_extension_missions_to_their_owner(repository):
    extensions.install()
    roundtable = _seed(repository, "m_rt", status=MissionStatus.RUNNING, room_mode=RoomMode.ROUNDTABLE)
    adaptive = _seed(repository, "m_work", status=MissionStatus.RUNNING)
    repository.add_event(adaptive.id, "adaptive.claimed", {})
    plain = _seed(repository, "m_plain", status=MissionStatus.RUNNING)
    _owned_by(repository, plain, _dead_pid())
    assert extension_points.any("mission_owned", repository, roundtable)

    interruption.recover(repository)

    for owned in (roundtable, adaptive):
        assert repository.get_mission(owned.id).status == MissionStatus.RUNNING
        assert not any(e.event_type == interruption.INTERRUPTED_EVENT
                       for e in repository.list_events_after(owned.id, 0))
    assert repository.get_mission(plain.id).status == MissionStatus.BLOCKED


def test_unfinished_turn_follows_provider_fallback(repository):
    mission = _seed(repository)
    def add(kind, attempt):
        repository.add_event(mission.id, kind, {"turn_id": "t1", "attempt": attempt})
    add("turn.started", 1)
    add("turn.blocked", 1)
    add("turn.started", 2)
    assert interruption.unfinished_turn(repository.list_events_after(mission.id, 0))["attempt"] == 2
    add("turn.completed", 2)
    assert interruption.unfinished_turn(repository.list_events_after(mission.id, 0)) is None


def test_app_built_beside_a_live_backend_leaves_its_runs_alone(repository, temp_settings):
    # Tests and smoke scripts build the app against databases a live backend may serve.
    running = _seed(repository, "m_live", status=MissionStatus.RUNNING)
    _owned_by(repository, running, os.getppid())
    starting = _seed(repository, "m_starting", status=MissionStatus.PENDING)
    with TestClient(create_app(temp_settings)):
        pass
    assert repository.get_mission(running.id).status == MissionStatus.RUNNING
    assert repository.get_mission(starting.id).status == MissionStatus.PENDING


def test_sending_a_message_unlocks_a_conversation_whose_runner_died(repository, temp_settings):
    with TestClient(create_app(temp_settings)) as client:
        orphan = _seed(repository, "m_orphan", status=MissionStatus.RUNNING)
        _owned_by(repository, orphan, _dead_pid())
        reply = client.post("/api/missions", json={"goal": "还在吗", "conversation_id": orphan.conversation_id})
    assert reply.status_code == 201
    assert repository.get_mission(orphan.id).status == MissionStatus.BLOCKED
