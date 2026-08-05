from backend.app.api.mission_detail import build_mission_detail
from backend.app.domain.models import Conversation, Mission
from backend.app.orchestration.live_progress import LiveProgressRegistry
from backend.app.orchestration import live_progress as live_progress_module


def _seed(repository):
    conv = Conversation(id="c_partial", title="t")
    repository.create_conversation(conv)
    mission = Mission(id="m_partial", title="P", goal="g", conversation_id=conv.id)
    repository.create_mission(mission)
    return mission


def test_snapshot_includes_live_partial_when_registry_has_text(repository, temp_settings, monkeypatch):
    mission = _seed(repository)
    isolated = LiveProgressRegistry()
    isolated.append_text(mission.id, "t_active", "正在思考")
    monkeypatch.setattr(live_progress_module, "_registry", isolated)

    detail = build_mission_detail(repository, temp_settings, mission.id)
    assert detail.get("live_partial") == {"t_active": "正在思考"}


def test_snapshot_omits_live_partial_when_registry_empty(repository, temp_settings, monkeypatch):
    mission = _seed(repository)
    isolated = LiveProgressRegistry()
    monkeypatch.setattr(live_progress_module, "_registry", isolated)

    detail = build_mission_detail(repository, temp_settings, mission.id)
    assert "live_partial" not in detail or detail["live_partial"] in (None, {})


def test_snapshot_live_partial_isolates_other_missions(repository, temp_settings, monkeypatch):
    mission = _seed(repository)
    isolated = LiveProgressRegistry()
    isolated.append_text(mission.id, "t_active", "本任务")
    isolated.append_text("other_mission", "t_other", "无关任务")
    monkeypatch.setattr(live_progress_module, "_registry", isolated)

    detail = build_mission_detail(repository, temp_settings, mission.id)
    assert detail.get("live_partial") == {"t_active": "本任务"}
