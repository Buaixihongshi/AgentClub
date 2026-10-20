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
    model: str = ""
    stop_reason: Optional[str] = None


@dataclass(frozen=True)
class StreamEvent:
    type: StreamEventType
    text: Optional[str] = None
    session_id: Optional[str] = None
    tool_name: Optional[str] = None
    activity_id: Optional[str] = None
    tool_phase: Optional[str] = None
    tool_summary: Optional[str] = None
    # Structured tool detail. `args` is the call's input, `output` what the CLI
    # printed back; both are sanitized before they reach the event table.
    tool_args: Optional[Dict] = None
    tool_output: Optional[str] = None
    tool_error: Optional[str] = None
    tool_ok: Optional[bool] = None
    model: Optional[str] = None
    error: Optional[str] = None
    usage: Optional[UsageRecord] = None
