from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class StreamEventType(str, Enum):
    SESSION_STARTED = "session_started"
    PARTIAL_TEXT = "partial_text"
    TOOL_ATTEMPT = "tool_attempt"
    FINISHED = "finished"
    ERROR = "error"


@dataclass(frozen=True)
class UsageRecord:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_cost_usd: Optional[float] = None
    duration_ms: int = 0
    provider_raw: Optional[Dict] = None


@dataclass(frozen=True)
class StreamEvent:
    type: StreamEventType
    text: Optional[str] = None
    session_id: Optional[str] = None
    tool_name: Optional[str] = None
    error: Optional[str] = None
    usage: Optional[UsageRecord] = None
