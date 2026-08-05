"""Tests for UsageRecord / StreamEvent usage extension and per-provider stream parsers."""

from pathlib import Path

from backend.app.adapters.stream_events import StreamEvent, StreamEventType, UsageRecord

FIXTURES = Path(__file__).parent / "fixtures" / "stream"


def test_usage_record_default_zeros():
    r = UsageRecord()
    assert r.input_tokens == 0
    assert r.cached_input_tokens == 0
    assert r.cache_creation_input_tokens == 0
    assert r.output_tokens == 0
    assert r.reasoning_output_tokens == 0
    assert r.total_cost_usd is None
    assert r.duration_ms == 0
    assert r.provider_raw is None


def test_stream_event_carries_usage():
    u = UsageRecord(input_tokens=10)
    ev = StreamEvent(type=StreamEventType.FINISHED, usage=u)
    assert ev.usage is not None
    assert ev.usage.input_tokens == 10


# ── S2: Codex parser ───────────────────────────────────────────────


def test_codex_parser_extracts_usage_from_turn_completed():
    from backend.app.adapters.stream_parsers.codex import parse

    lines = (FIXTURES / "codex_simple.jsonl").read_text().splitlines()
    turn_completed_line = [l for l in lines if l.startswith("{") and '"turn.completed"' in l][0]
    ev = parse(turn_completed_line)
    assert ev is not None
    assert ev.type == StreamEventType.FINISHED
    assert ev.usage is not None
    assert ev.usage.input_tokens == 24225
    assert ev.usage.cached_input_tokens == 6528
    assert ev.usage.output_tokens == 140
    assert ev.usage.reasoning_output_tokens == 120
    assert ev.usage.total_cost_usd is None
    assert ev.usage.provider_raw == {
        "input_tokens": 24225,
        "cached_input_tokens": 6528,
        "output_tokens": 140,
        "reasoning_output_tokens": 120,
    }


def test_codex_parser_handles_missing_usage():
    from backend.app.adapters.stream_parsers.codex import parse

    ev = parse('{"type":"turn.completed"}')
    assert ev is not None
    assert ev.type == StreamEventType.FINISHED
    assert ev.usage is None


# ── S3: Gemini parser ──────────────────────────────────────────────


def test_gemini_parser_extracts_usage_from_result():
    from backend.app.adapters.stream_parsers.gemini import parse

    lines = (FIXTURES / "gemini_simple.jsonl").read_text().splitlines()
    result_line = [l for l in lines if l.startswith("{") and '"result"' in l and '"stats"' in l][0]
    ev = parse(result_line)
    assert ev is not None
    assert ev.type == StreamEventType.FINISHED
    assert ev.usage is not None
    assert ev.usage.input_tokens == 14226
    assert ev.usage.cached_input_tokens == 0
    assert ev.usage.output_tokens == 86
    assert ev.usage.duration_ms == 9854
    assert ev.usage.total_cost_usd is None
    assert ev.usage.provider_raw is not None
    assert ev.usage.provider_raw["total_tokens"] == 15232
    assert "models" in ev.usage.provider_raw


def test_gemini_parser_handles_missing_stats():
    from backend.app.adapters.stream_parsers.gemini import parse

    ev = parse('{"type":"result","status":"success"}')
    assert ev is not None
    assert ev.type == StreamEventType.FINISHED
    assert ev.usage is None


# ── S4: Claude parser ──────────────────────────────────────────────


def test_claude_parser_extracts_usage_and_cost_from_result():
    from backend.app.adapters.stream_parsers.claude import parse

    lines = (FIXTURES / "claude_simple.jsonl").read_text().splitlines()
    result_line = [l for l in lines if l.startswith("{") and '"total_cost_usd"' in l][0]
    ev = parse(result_line)
    assert ev is not None
    assert ev.type == StreamEventType.FINISHED
    assert ev.usage is not None
    assert ev.usage.input_tokens == 6
    assert ev.usage.cached_input_tokens == 17843
    assert ev.usage.cache_creation_input_tokens == 14207
    assert ev.usage.output_tokens == 31
    assert ev.usage.total_cost_usd == 0.09852024999999999
    assert ev.usage.provider_raw is not None
    assert "usage" in ev.usage.provider_raw
    assert "modelUsage" in ev.usage.provider_raw


def test_claude_parser_ignores_intermediate_message_delta_usage():
    from backend.app.adapters.stream_parsers.claude import parse

    lines = (FIXTURES / "claude_simple.jsonl").read_text().splitlines()
    delta_lines = [
        l for l in lines
        if l.startswith("{") and "message_delta" in l and "usage" in l
    ]
    assert len(delta_lines) >= 1
    for line in delta_lines:
        ev = parse(line)
        if ev is not None:
            assert ev.usage is None, "message_delta should not produce usage"
