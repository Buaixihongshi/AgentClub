"""Adapters used by the evaluation harness.

``ScriptedAdapter`` replays fixture answers and optional A2A delegations. It is
for harness/CI validation only: the scores it produces are authored, not model
evidence. ``MeteredAdapter`` wraps any real adapter and records prompt/response
sizes so offline cost estimates remain available even when a provider does not
emit token usage.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from backend.app.adapters.base import CLIAdapter, TurnOutcome
from backend.app.domain.models import TurnStatus
from backend.app.mcp.tools import MCPToolError, MCPTools


TITLE_PREFIX = "EVAL:"


def parse_eval_title(title: str) -> Optional[tuple[str, str]]:
    if not title.startswith(TITLE_PREFIX):
        return None
    parts = title[len(TITLE_PREFIX) :].split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


@dataclass
class ScriptedSpec:
    final: str = ""
    draft: str = ""
    checks: List[str] = field(default_factory=list)
    dynamic: List[str] = field(default_factory=list)
    delegations: List[dict] = field(default_factory=list)


class ScriptedAdapter(CLIAdapter):
    """Replay fixture answers by *stage* rather than by raw call index.

    The evaluation arms share one 4-turn skeleton (draft -> check one ->
    check two -> revise), so the adapter selects content from the prompt's
    ``本轮`` line. Dynamic A2A callees are recognised by the host's
    "请你接手" section and replay the ``dynamic`` list in order.
    """

    def __init__(self, fixture_path: Path):
        raw = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        answers = raw.get("answers") if isinstance(raw, dict) else None
        if not isinstance(answers, dict):
            raise ValueError("fixture file needs an 'answers' object")
        self.answers: Dict[str, Dict[str, dict]] = answers
        self._dynamic_index: Dict[str, int] = {}
        self._fired: Dict[str, set] = {}

    def run_turn(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        prompt: str,
        tools: MCPTools,
    ) -> TurnOutcome:
        mission = tools.repository.get_mission(mission_id)
        parsed = self._task_and_arm(mission_id, mission, tools)
        spec = self._spec(parsed)
        stage = _stage_from_prompt(prompt)

        fired = self._fired.setdefault(mission_id, set())
        for index, delegation in enumerate(spec.delegations):
            if index in fired:
                continue
            if _normalize_stage(str(delegation.get("when", "draft"))) != stage:
                continue
            fired.add(index)
            try:
                tools.invoke_agent(
                    mission_id,
                    agent_id,
                    turn_id,
                    str(delegation.get("target_agent_id", "")),
                    str(delegation.get("purpose", "接力")),
                    provider_id,
                    mode=str(delegation.get("mode", "fire_and_forget")),
                )
            except MCPToolError:
                # A rejected delegation is part of the observed strategy: the
                # turn still completes and the trace records the tool error.
                pass

        content = self._content(spec, stage, mission_id)
        tools.publish_message(mission_id, agent_id, turn_id, content, provider_id)
        return TurnOutcome(status=TurnStatus.COMPLETED)

    def _task_and_arm(self, mission_id: str, mission, tools: MCPTools) -> Optional[tuple[str, str]]:
        for event in tools.repository.list_events_after(mission_id, 0):
            if event.event_type != "eval.metadata":
                continue
            task_id = event.payload.get("task_id")
            arm = event.payload.get("arm")
            if isinstance(task_id, str) and isinstance(arm, str):
                return task_id, arm
        return parse_eval_title(mission.title) if mission is not None else None

    def _spec(self, parsed: Optional[tuple[str, str]]) -> ScriptedSpec:
        if parsed is None:
            return ScriptedSpec(final="[scripted] missing eval metadata")
        task_id, arm = parsed
        raw = (self.answers.get(task_id) or {}).get(arm)
        if not isinstance(raw, dict):
            return ScriptedSpec(
                final="[scripted] no fixture for {task}/{arm}".format(task_id=task_id, arm=arm)
            )
        return ScriptedSpec(
            final=str(raw.get("final", "")),
            draft=str(raw.get("draft", "")),
            checks=[str(item) for item in raw.get("checks", [])],
            dynamic=[str(item) for item in raw.get("dynamic", [])],
            delegations=list(raw.get("delegations", [])),
        )

    def _content(self, spec: ScriptedSpec, stage: str, mission_id: str) -> str:
        if stage == "draft":
            return spec.draft or spec.final or "[scripted] empty draft"
        if stage == "check1":
            return spec.checks[0] if spec.checks else "检查一：未发现需要修改的问题。"
        if stage == "check2":
            return spec.checks[1] if len(spec.checks) > 1 else "检查二：未发现新增问题。"
        if stage == "dynamic":
            index = self._dynamic_index.get(mission_id, 0)
            self._dynamic_index[mission_id] = index + 1
            if index < len(spec.dynamic):
                return spec.dynamic[index]
            return "接力：已核对相关事实。"
        return spec.final or "[scripted] empty final"


def _stage_from_prompt(prompt: str) -> str:
    if "请你接手，" in prompt:
        return "dynamic"
    purpose = _turn_purpose(prompt)
    if "检查二" in purpose:
        return "check2"
    if "检查一" in purpose:
        return "check1"
    if "修订" in purpose:
        return "final"
    if "起草" in purpose:
        return "draft"
    return "final"


def _normalize_stage(value: str) -> str:
    return {
        "起草": "draft",
        "检查一": "check1",
        "检查二": "check2",
        "修订": "final",
    }.get(value, value)


def _turn_purpose(prompt: str) -> str:
    """Read only the current turn's purpose line, not prior messages in context."""
    marker = "\n本轮："
    index = prompt.rfind(marker)
    if index == -1:
        return prompt
    tail = prompt[index + len(marker) :]
    return tail.splitlines()[0] if tail.splitlines() else tail


@dataclass(frozen=True)
class TurnCall:
    agent_id: str
    turn_id: str
    provider_id: str
    prompt_chars: int
    response_chars: int
    status: str
    duration_ms: int


class MeteredAdapter(CLIAdapter):
    """Record prompt/response sizes around each delegated turn."""

    def __init__(self, inner: CLIAdapter):
        self.inner = inner
        self.calls: List[TurnCall] = []

    def run_turn(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        prompt: str,
        tools: MCPTools,
    ) -> TurnOutcome:
        before = {
            message.id
            for message in tools.repository.list_messages(mission_id)
            if message.role == "agent"
        }
        started = time.monotonic()
        try:
            outcome = self.inner.run_turn(
                mission_id, agent_id, turn_id, provider_id, prompt, tools
            )
        except Exception:
            self.calls.append(
                TurnCall(
                    agent_id=agent_id,
                    turn_id=turn_id,
                    provider_id=provider_id,
                    prompt_chars=len(prompt),
                    response_chars=0,
                    status="exception",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )
            raise
        after = [
            message
            for message in tools.repository.list_messages(mission_id)
            if message.role == "agent" and message.id not in before
        ]
        self.calls.append(
            TurnCall(
                agent_id=agent_id,
                turn_id=turn_id,
                provider_id=provider_id,
                prompt_chars=len(prompt),
                response_chars=sum(len(message.content) for message in after),
                status=getattr(outcome.status, "value", str(outcome.status)),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )
        return outcome

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()

    @property
    def estimated_input_tokens(self) -> int:
        return sum(_estimate_tokens(call.prompt_chars) for call in self.calls)

    @property
    def estimated_output_tokens(self) -> int:
        return sum(_estimate_tokens(call.response_chars) for call in self.calls)

    @property
    def max_prompt_chars(self) -> int:
        return max((call.prompt_chars for call in self.calls), default=0)


def _estimate_tokens(chars: int) -> int:
    # Deliberately conservative for mixed Chinese/English text.  Provider token
    # usage, when present, always wins over this estimate.
    return int(math.ceil(max(0, chars) / 3.0))
