from typing import Optional
from backend.app.adapters.stream_events import StreamEvent
from backend.app.adapters.stream_parsers import gemini as _gemini, codex as _codex, claude as _claude
from backend.app.adapters.stream_parsers.antigravity import AntigravityStreamParser


class StreamLineParser:
    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        self._claude = _claude.ClaudeStreamParser() if provider_id == "claude" else None
        self._antigravity = AntigravityStreamParser() if provider_id == "antigravity" else None

    def parse(self, line: str) -> Optional[StreamEvent]:
        line = line.strip()
        if not line:
            return None
        if self.provider_id == "gemini":
            return _gemini.parse(line)
        if self.provider_id == "codex":
            return _codex.parse(line)
        if self.provider_id == "claude" and self._claude is not None:
            return self._claude.parse(line)
        if self._antigravity is not None:
            return self._antigravity.parse(line)
        return None


def parse_stream_line(provider_id: str, line: str) -> Optional[StreamEvent]:
    return StreamLineParser(provider_id).parse(line)
