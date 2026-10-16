import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
from uuid import uuid4

from backend.app.adapters.base import CLIAdapter, TurnOutcome
from backend.app.domain.agent_registry import AgentRegistry, get_agent_registry
from backend.app.domain.models import (
    AgentProfile,
    Mission,
    MissionStatus,
    TurnStatus,
    utc_now,
    is_provider_verification_mission,
)
from backend.app.mcp.tools import MCPTools
from backend.app.settings import AgentOverride
from backend.app.orchestration.complexity import classify_task
from backend.app.orchestration.events import (
    fetch_a2a_requests_for_turn,
    find_pending_interrupts,
    record_a2a_cancelled,
    record_a2a_invoke,
    record_a2a_reply,
    record_a2a_reply_failed,
    record_a2a_skipped,
    record_interrupt_handled,
    record_turn_blocked,
    record_turn_completed,
    record_turn_failed,
    record_turn_started,
)
from backend.app.orchestration.context import build_artifact_index, build_conversation_context
from backend.app.orchestration.speaker import WorkflowStep, select_workflow
from backend.app.settings import Settings
from backend.app.storage.repository import Repository


@dataclass(frozen=True)
class PendingInterruptDecision:
    event_id: str
    content: str
    target_agent_id: Optional[str]


class MissionRunner:
    def __init__(
        self,
        repository: Repository,
        settings: Settings,
        adapter: CLIAdapter,
        agent_registry: Optional[AgentRegistry] = None,
    ):
        self.repository = repository
        self.settings = settings
        self.adapter = adapter
        self.agent_registry = agent_registry or get_agent_registry(settings.config_dir)
        self.agents = _apply_agent_overrides(
            self.agent_registry.all(), settings.agent_overrides,
        )

    def run(self, mission_id: str) -> Mission:
        try:
            return self._run_impl(mission_id)
        finally:
            close = getattr(self.adapter, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _run_impl(self, mission_id: str) -> Mission:
        mission = self.repository.get_mission(mission_id)
        if mission is None:
            raise ValueError("Unknown mission: {mission_id}".format(mission_id=mission_id))

        if mission.room_mode == 'roundtable':
            return self._run_roundtable(mission_id)

        complexity = classify_task(mission.goal)
        mission = mission.model_copy(
            update={
                "status": MissionStatus.RUNNING,
                "complexity": complexity,
                "updated_at": utc_now(),
            }
        )
        self.repository.update_mission(mission)

        tools = MCPTools(self.repository, self.settings.artifact_root)
        tools.update_status(mission_id, MissionStatus.RUNNING.value, summary="Mission started")

        workflow = select_workflow(
            mission.complexity,
            mission.goal,
            self.agents,
            target_agent_id=mission.target_agent_id,
            target_agent_ids=list(mission.target_agent_ids) or None,
        )
        agents = {agent.id: agent for agent in self.agents}
        providers = _providers_by_agent(self.agents)
        max_turns = max(0, self.settings.max_turns)
        max_a2a = max(0, self.settings.max_a2a_invocations)

        a2a_queue: deque[WorkflowStep] = deque()
        pending_replies: Dict[str, str] = {}
        a2a_used = 0
        workflow_cursor = 0
        turn_index = 0

        def enqueue_a2a(step: WorkflowStep) -> int:
            a2a_queue.append(step)
            return len(a2a_queue)

        while True:
            pending_interrupt = self._check_pending_interrupt(mission)
            if pending_interrupt is not None and (a2a_queue or pending_replies):
                cancelled_steps = [
                    {"step_id": s.step_id, "origin": s.origin, "agent_id": s.agent_id}
                    for s in a2a_queue
                ]
                if a2a_queue:
                    record_a2a_cancelled(
                        self.repository,
                        mission.id,
                        pending_count=len(a2a_queue),
                        reason="interrupt",
                        cancelled_steps=cancelled_steps,
                    )
                a2a_queue.clear()
                pending_replies.clear()

            # Consume the a2a queue before advancing the declared workflow so an
            # invoke_agent is honored right after the inviting turn ("就近兑现")
            # instead of drifting to the end of a multi-step workflow, where it
            # would re-litigate a topic the workflow already covered.
            if a2a_queue:
                step = a2a_queue.popleft()
                if step.origin == "a2a":
                    if a2a_used >= max_a2a:
                        return self._stop_mission(
                            mission,
                            MissionStatus.BLOCKED,
                            tools,
                            "a2a 深度耗尽 ({used}/{limit})".format(
                                used=a2a_used,
                                limit=max_a2a,
                            ),
                        )
                    a2a_used += 1
            elif workflow_cursor < len(workflow) and (
                workflow_cursor < max_turns or pending_interrupt is not None
            ):
                step = workflow[workflow_cursor]
                workflow_cursor += 1
            elif workflow_cursor < len(workflow):
                return self._stop_mission(
                    mission,
                    MissionStatus.BLOCKED,
                    tools,
                    "max_turns reached",
                )
            else:
                break

            agent_id_override = _interrupt_agent_override(pending_interrupt, agents)
            effective_agent_id = agent_id_override or step.agent_id
            if effective_agent_id not in agents:
                effective_agent_id = step.agent_id

            turn_index += 1
            turn_id = "turn_{index}_{suffix}".format(index=turn_index, suffix=uuid4().hex[:8])
            turn_purpose = _build_turn_purpose(step.purpose, pending_interrupt)
            a2a_step = step if step.origin in ("a2a", "a2a_resume") else None

            a2a_reply_text = None
            if step.origin == "a2a_resume":
                a2a_reply_text = pending_replies.pop(step.step_id, "<no reply: missing>")

            artifact_index = build_artifact_index(self.repository, mission.id)
            conversation_context = ""
            if not is_provider_verification_mission(mission):
                conversation_context = build_conversation_context(
                    self.repository, mission.conversation_id, mission_id=mission.id,
                    reply_to_turn_id=step.sender_turn_id,
                    agent_registry=self.agent_registry,
                )
            prompt = _build_prompt(
                mission,
                turn_purpose,
                agents[effective_agent_id],
                conversation_context=conversation_context,
                artifact_index=artifact_index,
                a2a_step=a2a_step,
                a2a_reply_text=a2a_reply_text,
                agents_by_id=agents,
                max_a2a_invocations=max_a2a,
            )

            def peek_resume_chain(sender_agent_id: str) -> list:
                chain: list = []
                for s in a2a_queue:
                    if s.origin == "a2a_resume" and s.sender_agent_id:
                        chain.append(s.sender_agent_id)
                return chain if sender_agent_id in chain else []

            remaining_workflow_agents_for_turn = {
                s.agent_id for s in workflow[workflow_cursor:]
            }
            turn_tools = MCPTools(
                self.repository,
                self.settings.artifact_root,
                a2a_enqueue=enqueue_a2a,
                valid_agent_ids=set(agents.keys()),
                max_a2a_invocations=max_a2a,
                a2a_budget_used=lambda: a2a_used + len(a2a_queue),
                peek_resume_chain=peek_resume_chain,
                is_target_scheduled=lambda t: t in remaining_workflow_agents_for_turn,
                current_step_origin=step.origin,
            )

            provider_candidates = providers[effective_agent_id]
            a2a_sender_turn_id = a2a_step.sender_turn_id if a2a_step else None
            outcome = self._run_turn_with_fallbacks(
                mission,
                effective_agent_id,
                turn_id,
                provider_candidates,
                prompt,
                turn_tools,
                a2a_sender_turn_id=a2a_sender_turn_id,
            )

            if step.origin == "a2a":
                self._handle_callee_reply(
                    mission.id, step, turn_id, effective_agent_id, outcome, a2a_queue, pending_replies,
                )

            if outcome.status == TurnStatus.COMPLETED:
                if step.is_closing:
                    # The declared workflow has concluded. Freeze the a2a queue so
                    # nothing re-litigates a closed discussion: drop pending steps
                    # and absorb any request this closing turn itself emitted.
                    self._freeze_a2a_on_close(
                        mission.id,
                        turn_id,
                        set(agents.keys()),
                        a2a_queue,
                        pending_replies,
                    )
                else:
                    remaining_workflow_agents = {
                        s.agent_id for s in workflow[workflow_cursor:]
                    }
                    budget_exceeded = self._pickup_a2a_requests(
                        mission.id,
                        turn_id,
                        set(agents.keys()),
                        a2a_queue,
                        lambda: a2a_used + len(a2a_queue),
                        max_a2a,
                        remaining_workflow_agents,
                    )
                    if budget_exceeded:
                        if pending_interrupt is not None:
                            record_interrupt_handled(
                                self.repository,
                                mission.id,
                                pending_interrupt.event_id,
                                pending_interrupt.target_agent_id,
                                effective_agent_id,
                                turn_id,
                            )
                        return self._stop_mission(
                            mission,
                            MissionStatus.BLOCKED,
                            tools,
                            "a2a 深度耗尽 (pickup exceeded budget {limit})".format(limit=max_a2a),
                        )
            if pending_interrupt is not None:
                record_interrupt_handled(
                    self.repository,
                    mission.id,
                    pending_interrupt.event_id,
                    pending_interrupt.target_agent_id,
                    effective_agent_id,
                    turn_id,
                )
            if outcome.status != TurnStatus.COMPLETED:
                if step.origin == "a2a" and _find_paired_resume_step(step.step_id, a2a_queue):
                    continue
                outcome_value = outcome.status.value
                status = (
                    MissionStatus.FAILED
                    if outcome.status == TurnStatus.FAILED
                    else MissionStatus.BLOCKED
                )
                return self._stop_mission(
                    mission,
                    status,
                    tools,
                    outcome.summary
                    or "Mission stopped after {agent_id} returned {outcome}".format(
                        agent_id=effective_agent_id, outcome=outcome_value
                    ),
                )

        completed_at = utc_now()
        mission = mission.model_copy(
            update={
                "status": MissionStatus.COMPLETED,
                "updated_at": completed_at,
                "completed_at": completed_at,
            }
        )
        self.repository.update_mission(mission)
        tools.update_status(mission_id, MissionStatus.COMPLETED.value, summary="Mission completed")
        return mission

    def _run_roundtable(self, mission_id):
        from backend.app.orchestration.roundtable import Roundtable
        policy = Roundtable(self.repository)
        try:
            while True:
                claimed = policy.claim(mission_id)
                if claimed is None:
                    return self.repository.get_mission(mission_id)
                mission, state, step, turn_id, notes = claimed
                agents = {a.id: a for a in self.agents if a.id in mission.discussion.participant_ids}
                context = build_conversation_context(
                    self.repository, mission.conversation_id, mission_id=mission_id,
                    reply_to_turn_id=step.sender_turn_id, agent_registry=self.agent_registry,
                )
                reply = None
                if step.origin == 'a2a_resume':
                    callee_turn = next((e.payload['turn_id'] for e in self.repository.list_events_after(mission_id, 0)
                                        if e.event_type == 'discussion.turn_claimed' and e.payload.get('step_id') == step.parent_step_id), None)
                    reply = '\n'.join(m.content for m in self.repository.list_messages(mission_id) if m.turn_id == callee_turn and m.role == 'agent')[:1500]
                purpose = '圆桌讨论：仅交流观点，不执行代码修改或自动生成文件。\n' + step.purpose + '\n' + notes + '\n参与者：{}；阶段：{}；已用回合：{}/{}（含当前回合，总结占一回合）。'.format(
                    ', '.join(mission.discussion.participant_ids), state['phase'], state['turns_used'], mission.discussion.max_turns)
                if step.is_closing:
                    purpose += '\n当前为总结，不再邀请其他角色。'
                else:
                    purpose += ('\n其他参与者已有基础发言安排；只有确需特定接力时才调 invoke_agent。'
                                '需要对方回复后续答时用 mode=wait_reply；接纳后结束当前回合，由宿主安排受邀者和你的续答。'
                                '邀请共享本场总回合预算；工具返回未接纳时继续当前发言，不承诺额外回合。')
                prompt = _build_prompt(mission, purpose, agents[step.agent_id], conversation_context=context,
                                       artifact_index=build_artifact_index(self.repository, mission_id),
                                       a2a_step=step if step.origin != 'workflow' else None, a2a_reply_text=reply,
                                       agents_by_id=agents, max_a2a_invocations=None)
                tools = MCPTools(self.repository, self.settings.artifact_root,
                                 valid_agent_ids=set(agents), current_step_origin=step.origin)
                outcome = self._run_turn_with_fallbacks(mission, step.agent_id, turn_id,
                                                       _providers_by_agent(list(agents.values()))[step.agent_id], prompt, tools,
                                                       a2a_sender_turn_id=step.sender_turn_id)
                policy.complete(mission_id, turn_id, outcome)
        except Exception:
            policy.fail(mission_id)
            raise

    def _check_pending_interrupt(
        self,
        mission: Mission,
    ) -> Optional[PendingInterruptDecision]:
        """Return the oldest pending interrupt, if any."""
        pending = find_pending_interrupts(self.repository, mission.id)
        if not pending:
            return None
        interrupt = pending[0]
        target = interrupt.payload.get("target_agent_id")
        content = str(interrupt.payload.get("content") or "")
        return PendingInterruptDecision(
            event_id=str(interrupt.id),
            content=content,
            target_agent_id=target,
        )

    def _stop_mission(
        self,
        mission: Mission,
        status: MissionStatus,
        tools: MCPTools,
        summary: str,
    ) -> Mission:
        updated_at = utc_now()
        mission = mission.model_copy(
            update={
                "status": status,
                "updated_at": updated_at,
            }
        )
        self.repository.update_mission(mission)
        tools.update_status(mission.id, status.value, summary=summary)
        return mission

    def _handle_callee_reply(
        self,
        mission_id: str,
        step: WorkflowStep,
        turn_id: str,
        callee_agent_id: str,
        outcome: TurnOutcome,
        a2a_queue: "deque[WorkflowStep]",
        pending_replies: Dict[str, str],
    ) -> None:
        resume_step = _find_paired_resume_step(step.step_id, a2a_queue)
        if resume_step is None:
            return
        invoke_event_id = _find_invoke_event_id(
            self.repository, mission_id, step.sender_turn_id,
        )
        if outcome.status == TurnStatus.COMPLETED:
            extraction = _extract_reply_text(self.repository, mission_id, turn_id)
            if extraction.found:
                record_a2a_reply(
                    self.repository, mission_id,
                    callee_turn_id=turn_id,
                    callee_agent_id=callee_agent_id,
                    invoke_event_id=invoke_event_id or "",
                    sender_agent_id=step.sender_agent_id or "",
                    sender_resume_step_id=resume_step.step_id,
                    reply_text=extraction.text,
                    reply_message_id=extraction.message_id,
                    truncated=extraction.truncated,
                )
                pending_replies[resume_step.step_id] = extraction.text
            else:
                fallback = "<no reply: empty>"
                record_a2a_reply_failed(
                    self.repository, mission_id,
                    callee_turn_id=turn_id,
                    callee_agent_id=callee_agent_id,
                    invoke_event_id=invoke_event_id or "",
                    reason="callee turn produced no agent message",
                    fallback_reply_text=fallback,
                )
                pending_replies[resume_step.step_id] = fallback
        else:
            fallback = "<no reply: turn {s}>".format(s=outcome.status.value)
            pending_replies[resume_step.step_id] = fallback
            record_a2a_reply_failed(
                self.repository, mission_id,
                callee_turn_id=turn_id,
                callee_agent_id=callee_agent_id,
                invoke_event_id=invoke_event_id or "",
                reason=outcome.summary or "callee turn {s}".format(s=outcome.status.value),
                fallback_reply_text=fallback,
            )

    def _freeze_a2a_on_close(
        self,
        mission_id: str,
        closing_turn_id: str,
        valid_agent_ids: set,
        a2a_queue: "deque[WorkflowStep]",
        pending_replies: dict,
    ) -> None:
        """Freeze A2A once the workflow's closing turn has run.

        Pending queued steps are cancelled and any a2a.request the closing turn
        emitted is absorbed (a2a.skipped) instead of materialized — both would
        otherwise produce a turn after the conclusion, re-opening a closed topic.
        """
        if a2a_queue:
            record_a2a_cancelled(
                self.repository,
                mission_id,
                pending_count=len(a2a_queue),
                reason="workflow closed",
                cancelled_steps=[
                    {"step_id": s.step_id, "origin": s.origin, "agent_id": s.agent_id}
                    for s in a2a_queue
                ],
            )
            a2a_queue.clear()
        pending_replies.clear()
        for event in fetch_a2a_requests_for_turn(self.repository, mission_id, closing_turn_id):
            target = event.payload.get("target_agent_id", "")
            if target not in valid_agent_ids:
                continue
            record_a2a_skipped(
                self.repository,
                mission_id,
                event.payload.get("sender_agent_id", ""),
                closing_turn_id,
                target,
                event.payload.get("purpose", ""),
                reason="workflow closed",
            )

    def _pickup_a2a_requests(
        self,
        mission_id: str,
        sender_turn_id: str,
        valid_agent_ids: set,
        a2a_queue: "deque[WorkflowStep]",
        budget_used: "Callable[[], int]",
        max_a2a: int,
        remaining_workflow_agents: Optional[set] = None,
    ) -> bool:
        """Pick up a2a.request events written by a subprocess MCP call during turn.

        For each valid, in-budget request, record an a2a.invoke event and
        append the step to the worklist.  Invalid targets are silently skipped.

        A fire-and-forget request whose target is still scheduled to speak in the
        remaining declared workflow is absorbed (recorded as a2a.skipped) rather
        than queued, to avoid a redundant duplicate turn.  wait_reply requests are
        never absorbed — the sender is blocked waiting for that specific reply.

        Returns True if a budget overflow was detected (caller should BLOCK the
        mission), False otherwise.
        """
        remaining = remaining_workflow_agents or set()
        requests = fetch_a2a_requests_for_turn(self.repository, mission_id, sender_turn_id)
        for event in requests:
            target = event.payload.get("target_agent_id", "")
            if target not in valid_agent_ids:
                continue
            purpose = event.payload.get("purpose", "")
            mode = event.payload.get("mode", "fire_and_forget")
            sender_agent_id = event.payload.get("sender_agent_id", "")
            if mode == "fire_and_forget" and target in remaining:
                # Target already speaks later in the declared workflow — queuing an
                # a2a turn for them is a redundant duplicate. Absorb it.
                record_a2a_skipped(
                    self.repository,
                    mission_id,
                    sender_agent_id,
                    sender_turn_id,
                    target,
                    purpose,
                    reason="target already scheduled in workflow",
                )
                continue
            if budget_used() >= max_a2a:
                return True
            callee_step = WorkflowStep(
                agent_id=target,
                purpose=purpose,
                origin="a2a",
                sender_agent_id=sender_agent_id,
                sender_turn_id=sender_turn_id,
            )
            a2a_queue.append(callee_step)
            record_a2a_invoke(
                self.repository,
                mission_id,
                sender_agent_id,
                sender_turn_id,
                target,
                purpose,
                mode,
                len(a2a_queue),
            )
            if mode == "wait_reply":
                resume_step = WorkflowStep(
                    agent_id=sender_agent_id,
                    purpose="承接 {target} 对『{purpose}』的回复".format(
                        target=target, purpose=purpose,
                    ),
                    origin="a2a_resume",
                    sender_agent_id=sender_agent_id,
                    sender_turn_id=sender_turn_id,
                    parent_step_id=callee_step.step_id,
                )
                a2a_queue.append(resume_step)
        return False

    def _run_turn_with_fallbacks(
        self,
        mission: Mission,
        agent_id: str,
        turn_id: str,
        provider_candidates: List[str],
        prompt: str,
        tools: MCPTools,
        *,
        a2a_sender_turn_id: Optional[str] = None,
    ) -> TurnOutcome:
        last_outcome = TurnOutcome(
            status=TurnStatus.BLOCKED,
            summary="No provider candidates configured for {agent_id}".format(
                agent_id=agent_id
            ),
        )
        for index, provider_id in enumerate(provider_candidates):
            attempt = index + 1
            if mission.room_mode == 'roundtable':
                from backend.app.orchestration.roundtable import Roundtable
                Roundtable(self.repository).authorize_attempt(mission.id, turn_id, provider_id, attempt)
            record_turn_started(
                self.repository,
                mission.id,
                turn_id,
                agent_id,
                provider_id,
                attempt,
                prompt[:80],
                a2a_sender_turn_id=a2a_sender_turn_id,
            )
            t0 = time.monotonic()
            try:
                outcome = self.adapter.run_turn(
                    mission.id,
                    agent_id,
                    turn_id,
                    provider_id,
                    prompt,
                    tools,
                )
            except Exception as exc:
                duration_ms = int((time.monotonic() - t0) * 1000)
                record_turn_failed(
                    self.repository,
                    mission.id,
                    turn_id,
                    agent_id,
                    provider_id,
                    attempt,
                    duration_ms,
                    str(exc),
                )
                return TurnOutcome(
                    status=TurnStatus.FAILED,
                    summary="Adapter failed: {error}".format(error=exc),
                )

            duration_ms = int((time.monotonic() - t0) * 1000)
            outcome = _normalize_outcome(outcome)
            if outcome.status == TurnStatus.COMPLETED:
                record_turn_completed(
                    self.repository,
                    mission.id,
                    turn_id,
                    agent_id,
                    provider_id,
                    attempt,
                    duration_ms,
                    outcome.summary or "completed",
                )
                return outcome

            last_outcome = outcome
            record_turn_blocked(
                self.repository,
                mission.id,
                turn_id,
                agent_id,
                provider_id,
                attempt,
                duration_ms,
                outcome.summary or "blocked",
            )
            if index < len(provider_candidates) - 1:
                tools.update_status(
                    mission.id,
                    MissionStatus.RUNNING.value,
                    summary=(
                        "{agent_id} provider {provider_id} blocked; trying fallback"
                    ).format(agent_id=agent_id, provider_id=provider_id),
                )

        return last_outcome


def _providers_by_agent(agents: List[AgentProfile]) -> Dict[str, List[str]]:
    return {
        agent.id: [agent.default_provider_id, *agent.fallback_provider_ids]
        for agent in agents
    }


def _apply_agent_overrides(
    base: List[AgentProfile],
    overrides: Dict[str, AgentOverride],
) -> List[AgentProfile]:
    if not overrides:
        return base
    result: List[AgentProfile] = []
    for agent in base:
        ov = overrides.get(agent.id)
        if ov is None:
            result.append(agent)
            continue
        updates: Dict[str, object] = {}
        if ov.default_provider_id is not None:
            updates["default_provider_id"] = ov.default_provider_id
        if ov.fallback_provider_ids is not None:
            updates["fallback_provider_ids"] = list(ov.fallback_provider_ids)
        result.append(agent.model_copy(update=updates) if updates else agent)
    return result


def _interrupt_agent_override(
    pending_interrupt: Optional[PendingInterruptDecision],
    agents: Dict[str, AgentProfile],
) -> Optional[str]:
    if pending_interrupt is None:
        return None
    target = pending_interrupt.target_agent_id
    if target and target in agents:
        return target
    return None


def _build_turn_purpose(
    base_purpose: str,
    pending_interrupt: Optional[PendingInterruptDecision],
) -> str:
    if pending_interrupt is None:
        return base_purpose
    target = pending_interrupt.target_agent_id or "未指定"
    content = pending_interrupt.content or "（空）"
    return (
        "优先处理用户插话：{content}\n"
        "插话目标：{target}\n"
        "原定回合目的：{base_purpose}"
    ).format(content=content, target=target, base_purpose=base_purpose)


_REPLY_MAX_CHARS = 1500


@dataclass(frozen=True)
class _ReplyExtraction:
    text: str
    message_id: Optional[str]
    found: bool
    truncated: bool


def _extract_reply_text(
    repository: Repository,
    mission_id: str,
    callee_turn_id: str,
) -> "_ReplyExtraction":
    events = repository.list_events_for_turn(mission_id, callee_turn_id)
    messages = [
        e for e in events
        if e.event_type == "message" and e.payload.get("role") == "agent"
    ]
    if not messages:
        return _ReplyExtraction("", None, False, False)
    last = messages[-1]
    text = str(last.payload.get("content") or "")
    truncated = False
    if len(text) > _REPLY_MAX_CHARS:
        text = text[:_REPLY_MAX_CHARS] + "…"
        truncated = True
    return _ReplyExtraction(text, last.payload.get("message_id"), True, truncated)


def _find_paired_resume_step(
    callee_step_id: str,
    a2a_queue: "deque[WorkflowStep]",
) -> Optional[WorkflowStep]:
    for step in a2a_queue:
        if step.origin == "a2a_resume" and step.parent_step_id == callee_step_id:
            return step
    return None


def _find_invoke_event_id(
    repository: Repository,
    mission_id: str,
    sender_turn_id: Optional[str],
) -> Optional[str]:
    if not sender_turn_id:
        return None
    events = repository.list_events_after(mission_id, 0)
    for e in reversed(events):
        if e.event_type == "a2a.invoke" and e.payload.get("sender_turn_id") == sender_turn_id:
            return str(e.id)
    return None


def _normalize_outcome(outcome) -> TurnOutcome:
    if isinstance(outcome, TurnOutcome):
        return outcome
    if isinstance(outcome, TurnStatus):
        return TurnOutcome(status=outcome)
    try:
        return TurnOutcome(status=TurnStatus(str(outcome)))
    except ValueError:
        return TurnOutcome(
            status=TurnStatus.FAILED,
            summary="Adapter returned invalid outcome: {outcome}".format(outcome=outcome),
        )


def _build_prompt(
    mission: Mission,
    purpose: str,
    agent: AgentProfile,
    *,
    conversation_context: str = "",
    artifact_index: str = "",
    a2a_step: Optional[WorkflowStep] = None,
    a2a_reply_text: Optional[str] = None,
    agents_by_id: Optional[Dict[str, AgentProfile]] = None,
    max_a2a_invocations: Optional[int] = None,
) -> str:
    parts = [
        "聊天室模式：你正在 AgentClub 的多 Agent 房间中发言。\n"
        "你是{display_name}，性格特点是{role}。人格与交流习惯：{persona}\n"
        "【共同原则】三位伙伴都可以提方案、分析、执行和总结；人格决定关注点与交流方式，不限定能力或工种。主持、质疑和收束只是本轮需要时的临时行为。\n"
        "人格保持稳定，立场随事实与交流改变；回应别人具体说过的内容，不重复原问题的答案，不为显得不同而强行反对或附和。没有新观点时简短说明，不编造分歧。\n"
        "性格不能成为隐瞒事实、降低准确性或拒绝已授权工作的理由；不扮演全知者，不靠口头禅和方言代替观点。\n"
        "只回复当前角色这一轮该说的话，不要代替其他角色发言，不要输出 Markdown 标题。\n"
        "【交付边界】任务复杂度只决定协作轮次，不决定是否交付文件。角色性格、轮次目的、被点名或接力请求都不能单独视为文件交付要求。\n"
        "结合用户当前请求和已有会话上下文判断：讨论、比较、解释、评审修改意见时，直接在聊天中给观点、实例和建议，不主动创建或登记文件。\n"
        "只有用户明确要求文件交付，或完成已获授权的执行任务确实需要文件时，才读写相应文件；用户确认执行前文方案时，按前文范围落实。\n"
        "如需读写文件或执行命令，使用你 CLI 自带的工具；只有确需展示的最终交付文件才用 write_artifact 登记，不为每轮发言额外生成总结文件。\n"
        "如本轮明确需要交付文件，可返回 JSON envelope："
        '{{"message":"本轮回复","artifacts":[{{"path":"summary.md","content":"# Summary"}}]}}；'
        "否则只返回普通中文回复；按任务需要详略，不设置固定字数上限。".format(
            display_name=agent.display_name,
            role=agent.role,
            persona=agent.persona,
        ),
    ]
    if agent.trigger_points:
        bullets = "\n".join("- {tp}".format(tp=tp) for tp in agent.trigger_points)
        parts.append(
            "【自然关注点】轮到你时，按当前话题选择有帮助的角度，不必逐条回答，也不要抢着承担固定职责：\n"
            "{bullets}".format(bullets=bullets)
        )
    if conversation_context:
        parts.append(conversation_context)
    if artifact_index:
        parts.append(artifact_index)
    if a2a_step is not None and a2a_step.origin == "a2a":
        sender_name = a2a_step.sender_agent_id or "上一个 agent"
        if agents_by_id and a2a_step.sender_agent_id in agents_by_id:
            sender_name = agents_by_id[a2a_step.sender_agent_id].display_name
        parts.append(
            "{receiver}，{sender}（在 {turn_id}）请你接力：{purpose}\n"
            "请直接基于上面的对话历史回答。".format(
                receiver=agent.display_name,
                sender=sender_name,
                turn_id=a2a_step.sender_turn_id or "未知 turn",
                purpose=a2a_step.purpose,
            )
        )
    if a2a_step is not None and a2a_step.origin == "a2a_resume" and a2a_reply_text is not None:
        parts.append(
            "【接续上一轮】{purpose}\n"
            "对方的回复：\n> {reply}\n"
            "请基于此回复继续推进任务，不要重复对方已经说过的内容。".format(
                purpose=a2a_step.purpose,
                reply=a2a_reply_text,
            )
        )
    parts.append(
        "Mission: {title}\n"
        "Goal: {goal}\n"
        "Turn purpose: {purpose}".format(
            title=mission.title,
            goal=mission.goal,
            purpose=purpose,
        )
    )
    if max_a2a_invocations is not None:
        parts.append(
            "若需要其他角色接力处理某个子任务：\n"
            "- 不需要他们的回复就继续：调 invoke_agent(target, purpose)\n"
            "- 必须看到他们的回复才能继续推理：调 invoke_agent(target, purpose, mode=\"wait_reply\")\n"
            "- 不能只在正文里喊对方名字或说“你来收尾”；正文点名只是展示文本，不会触发 A2A。\n"
            "  你的本回合到此结束，下一轮你会被重新唤起、prompt 中会带上对方回复。\n"
            "每个 mission 最多召唤 {n} 次（fire_and_forget 与 wait_reply 共享同一预算）。".format(
                n=max_a2a_invocations,
            )
        )
    parts.append(
        "【发送前出口检查】（在心里过一遍再发；不需要把检查过程写出来）\n"
        "1. 这一轮你要做的事是否真的做完了？如果只完成一半，明说没做完的部分。\n"
        "2. 接下来由谁继续推进？如果该点名某个 agent 接力，用 invoke_agent；不能只在正文里喊对方名字或说“你来收尾”。如果你认为流程到此可以收口，直接说『到此为止』。\n"
        "3. 你引述别人写的内容时，路径/数字/原话有没有用 read_artifact 核对过？没核对就别断言。\n"
        "4. **关键判断/复杂方案产出前，请务必先调 log_thought 工具记录思考过程**；这能让你的逻辑在 timeline 上可视化，且不会污染后续回合的 prompt 长度。"
    )
    return "\n".join(parts)
