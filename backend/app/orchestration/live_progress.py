import threading
from typing import Dict, Tuple


class LiveProgressRegistry:
    def __init__(self, max_chars_per_turn: int = 8192):
        self._max_chars = max_chars_per_turn
        self._lock = threading.Lock()
        self._text: Dict[Tuple[str, str], str] = {}

    def append_text(self, mission_id: str, turn_id: str, chunk: str) -> None:
        if not chunk:
            return
        key = (mission_id, turn_id)
        with self._lock:
            current = self._text.get(key, "") + chunk
            if len(current) > self._max_chars:
                current = current[-self._max_chars:]
            self._text[key] = current

    def mark_active(self, mission_id: str, turn_id: str) -> None:
        # Reserve the key so a streaming bubble surfaces immediately, even
        # before any partial text arrives (Gemini/Codex emit no token deltas
        # — without this the typing-dots bubble would only appear at the very
        # end of the turn).
        key = (mission_id, turn_id)
        with self._lock:
            self._text.setdefault(key, "")

    def get_partial_text(self, mission_id: str, turn_id: str) -> str:
        with self._lock:
            return self._text.get((mission_id, turn_id), "")

    def clear(self, mission_id: str, turn_id: str) -> None:
        key = (mission_id, turn_id)
        with self._lock:
            self._text.pop(key, None)

    def clear_mission(self, mission_id: str) -> None:
        with self._lock:
            self._text = {k: v for k, v in self._text.items() if k[0] != mission_id}

    def snapshot_for_mission(self, mission_id: str) -> Dict[str, str]:
        with self._lock:
            return {turn_id: text for (mid, turn_id), text in self._text.items() if mid == mission_id}


_registry = LiveProgressRegistry()


def get_live_progress_registry() -> LiveProgressRegistry:
    return _registry
