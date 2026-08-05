import threading
from backend.app.orchestration.live_progress import LiveProgressRegistry


def test_append_and_read_partial_text():
    reg = LiveProgressRegistry(max_chars_per_turn=128)
    reg.append_text("m1", "t1", "你好")
    reg.append_text("m1", "t1", "世界")
    assert reg.get_partial_text("m1", "t1") == "你好世界"


def test_clear_drops_buffer():
    reg = LiveProgressRegistry(max_chars_per_turn=128)
    reg.append_text("m1", "t1", "abc")
    reg.clear("m1", "t1")
    assert reg.get_partial_text("m1", "t1") == ""


def test_snapshot_for_mission_returns_active_turns_only():
    reg = LiveProgressRegistry(max_chars_per_turn=128)
    reg.append_text("m1", "t1", "alpha")
    reg.append_text("m1", "t2", "beta")
    snap = reg.snapshot_for_mission("m1")
    assert snap == {"t1": "alpha", "t2": "beta"}


def test_cap_truncates_old_chars():
    reg = LiveProgressRegistry(max_chars_per_turn=4)
    reg.append_text("m1", "t1", "abcdefgh")
    assert reg.get_partial_text("m1", "t1") == "efgh"


def test_mark_active_reserves_empty_entry():
    reg = LiveProgressRegistry(max_chars_per_turn=128)
    reg.mark_active("m1", "t1")
    assert reg.snapshot_for_mission("m1") == {"t1": ""}
    # Subsequent appends should accumulate normally.
    reg.append_text("m1", "t1", "ok")
    assert reg.get_partial_text("m1", "t1") == "ok"


def test_mark_active_does_not_overwrite_existing_text():
    reg = LiveProgressRegistry(max_chars_per_turn=128)
    reg.append_text("m1", "t1", "已有内容")
    reg.mark_active("m1", "t1")
    assert reg.get_partial_text("m1", "t1") == "已有内容"


def test_concurrent_writes_safe():
    reg = LiveProgressRegistry(max_chars_per_turn=10000)

    def worker(prefix: str):
        for i in range(100):
            reg.append_text("m1", "t1", f"{prefix}{i}")

    threads = [threading.Thread(target=worker, args=(p,)) for p in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    text = reg.get_partial_text("m1", "t1")
    assert "a99" in text and "b99" in text
