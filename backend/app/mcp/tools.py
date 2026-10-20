import hashlib
from functools import wraps
from inspect import signature

from pathlib import Path
from typing import Callable, Dict, List, Optional, Set
from uuid import uuid4

from backend.app.domain.history import select_context_messages
from backend.app.domain.models import (
    Message,
    MessageRole,
    is_provider_verification_mission,
)
from backend.app.orchestration.events import (
    record_a2a_invoke,
    record_a2a_request,
    record_a2a_skipped,
    record_tool_call,
    record_tool_error,
    record_tool_result,
)
from backend.app.orchestration.speaker import WorkflowStep
from backend.app.storage.repository import Repository


def _clip_text(value: object, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _context_digest(view: dict) -> str:
    """A readable summary of the frozen context an agent just read.

    The raw view is mostly ids; the useful part is the task, the other agents'
    positions, the open issues and which materials are in play. This is what
    the observability panel shows instead of "Returned frozen phase context".
    """
    snapshot = view.get("snapshot") if isinstance(view.get("snapshot"), dict) else {}
    results = view.get("results") if isinstance(view.get("results"), list) else []
    issues = view.get("issues") if isinstance(view.get("issues"), dict) else {}
    materials = snapshot.get("materials") if isinstance(snapshot.get("materials"), dict) else {}
    paths = [str(item.get("path")) for item in materials.values()
             if isinstance(item, dict) and item.get("path")]
    lines = ["phase={phase} · round={round} · results={r} · issues={i} · materials={m} · history={h}".format(
        phase=view.get("phase") or "?",
        round=view.get("round_index", 0),
        r=len(results), i=len(issues), m=len(materials),
        h=snapshot.get("history_count") or 0)]
    task = snapshot.get("goal")
    if task:
        lines.append("任务：" + _clip_text(task, 140))
    for result in results[:3]:
        if not isinstance(result, dict):
            continue
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        text = data.get("position_summary") or data.get("content") or ""
        if not text:
            claims = data.get("claims") if isinstance(data.get("claims"), list) else []
            text = claims[0].get("statement") if claims and isinstance(claims[0], dict) else ""
        if not text:
            sections = data.get("sections") if isinstance(data.get("sections"), list) else []
            text = sections[0].get("content") if sections and isinstance(sections[0], dict) else ""
        if text:
            lines.append("- {agent}/{kind}: {text}".format(
                agent=result.get("agent_id") or "?", kind=result.get("kind") or "?",
                text=_clip_text(text, 140)))
    for issue in list(issues.values())[:2]:
        if isinstance(issue, dict):
            lines.append("- issue/{cat}: {problem}".format(
                cat=issue.get("category") or "?", problem=_clip_text(issue.get("problem"), 120)))
    if paths:
        shown = "、".join(paths[:3]) + ("…" if len(paths) > 3 else "")
        lines.append("材料：" + shown)
    return "\n".join(lines)


def _mission_context_digest(result: dict) -> str:
    """The non-deliberation `read_context` answer: task plus recent messages."""
    mission = result.get("mission") if isinstance(result.get("mission"), dict) else {}
    messages = result.get("messages") if isinstance(result.get("messages"), list) else []
    lines = ["任务：" + _clip_text(mission.get("goal") or mission.get("title"), 140)]
    lines.append("历史消息：{n} 条".format(n=len(messages)))
    if messages and isinstance(messages[-1], dict):
        lines.append("最近：" + _clip_text(messages[-1].get("content"), 200))
    return "\n".join(lines)


class MCPToolError(ValueError):
    pass


def roundtable_guard(fn):
    call_signature = signature(fn)

    @wraps(fn)
    def guarded(self, *args, **kwargs):
        from backend.app.orchestration.roundtable import Roundtable, DiscussionError
        values = call_signature.bind(self, *args, **kwargs).arguments
        mission_id = values.get('mission_id')
        mission = self.repository.get_mission(mission_id)
        agent = values.get('agent_id') or values.get('sender_agent_id')
        turn = values.get('turn_id') or values.get('sender_turn_id')
        if mission is None or mission.room_mode != 'roundtable' or (fn.__name__ == 'update_status' and not agent):
            return fn(self, *args, **kwargs)
        if mission.discussion and mission.discussion.protocol == 'deliberation_v2':
            raise MCPToolError('V2 discussion only allows frozen context/source reads and structured submission')
        try:
            with Roundtable(self.repository).tool_scope(mission_id, agent, turn, values.get('provider_id')):
                if fn.__name__ == 'update_status' and values.get('status') != 'running':
                    raise DiscussionError('The host owns roundtable lifecycle; publish your conclusion as a message')
                return fn(self, *args, **kwargs)
        except (DiscussionError, MCPToolError) as exc:
            record_tool_error(self.repository, mission_id, turn or 'unknown', agent or 'unknown', fn.__name__, str(exc), values.get('provider_id'))
            raise MCPToolError(str(exc)) from exc
    return guarded


class MCPTools:
    def __init__(
        self,
        repository: Repository,
        artifact_root: Path,
        *,
        a2a_enqueue: Optional[Callable[[WorkflowStep], "int | None"]] = None,
        valid_agent_ids: Optional[Set[str]] = None,
        max_a2a_invocations: int = 0,
        a2a_budget_used: Optional[Callable[[], int]] = None,
        peek_resume_chain: Optional[Callable[[str], List[str]]] = None,
        is_target_scheduled: Optional[Callable[[str], bool]] = None,
        receipt_id: Optional[str] = None,
        current_step_origin: Optional[str] = None,
        lease_token: Optional[str] = None,
    ):
        self.repository = repository
        self.artifact_root = artifact_root
        self.a2a_enqueue = a2a_enqueue
        self.valid_agent_ids = set(valid_agent_ids or [])
        self.max_a2a_invocations = max_a2a_invocations
        self.a2a_budget_used = a2a_budget_used or (lambda: 0)
        self._peek_resume_chain = peek_resume_chain
        self._is_target_scheduled = is_target_scheduled
        self.receipt_id = receipt_id
        self._current_step_origin = current_step_origin
        self.lease_token = lease_token

    @property
    def current_step_origin(self) -> Optional[str]:
        """Workflow origin for the active turn, used by host execution policy."""
        return self._current_step_origin

    def read_context(
        self,
        mission_id: str,
        limit: int = 20,
        *,
        agent_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        provider_id: Optional[str] = None,
        material_offset: int = 0,
        history_offset: int = 0,
        issue_offset: int = 0,
    ) -> Dict[str, object]:
        from backend.app.orchestration.deliberation import Deliberation, is_v2
        if is_v2(self.repository.get_mission(mission_id)):
            try:
                result = Deliberation(self.repository).read_context(mission_id, agent_id, turn_id, provider_id, self.lease_token,
                    material_offset=material_offset, history_offset=history_offset, issue_offset=issue_offset)
                record_tool_call(self.repository, mission_id, turn_id, agent_id, 'read_context', {}, provider_id)
                record_tool_result(self.repository, mission_id, turn_id, agent_id, 'read_context',
                                   summary='Returned frozen phase context', provider_id=provider_id,
                                   output=_context_digest(result))
                return result
            except ValueError as exc:
                raise MCPToolError(str(exc)) from exc
        if agent_id and turn_id:
            record_tool_call(
                self.repository,
                mission_id,
                turn_id,
                agent_id,
                "read_context",
                {"limit": limit},
                provider_id,
            )
        mission = self.repository.get_mission(mission_id)
        if mission is None:
            raise MCPToolError(f"Unknown mission: {mission_id}")
        conversation_id = mission.conversation_id if not is_provider_verification_mission(mission) else None
        history = self.repository.read_message_history(
            conversation_id=conversation_id, mission_id=None if conversation_id else mission_id,
        )
        messages = select_context_messages(history.project(), limit)
        result = {
            "mission": mission.model_dump(mode="json"),
            "messages": [message.model_dump(mode="json") for message in messages],
        }
        if agent_id and turn_id:
            record_tool_result(
                self.repository,
                mission_id,
                turn_id,
                agent_id,
                "read_context",
                summary="Returned {n} messages".format(n=len(messages)),
                provider_id=provider_id,
                output=_mission_context_digest(result),
            )
        return result

    @roundtable_guard
    def publish_message(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        content: str,
        provider_id: str,
        *,
        trace: bool = True,
    ) -> Message:
        mission = self._require_mission(mission_id)
        if trace:
            record_tool_call(
                self.repository,
                mission_id,
                turn_id,
                agent_id,
                "publish_message",
                {"content_length": len(content)},
                provider_id,
            )
        message = Message(
            id=f"msg_{uuid4().hex}",
            mission_id=mission_id,
            conversation_id=mission.conversation_id,
            role=MessageRole.AGENT,
            agent_id=agent_id,
            turn_id=turn_id,
            provider_id=provider_id,
            content=content,
        )
        self.repository.add_message(message)
        if self.receipt_id:
            self.repository.add_event(
                mission_id,
                "mcp.receipt",
                {
                    "receipt_id": self.receipt_id,
                    "message_id": message.id,
                    "turn_id": turn_id,
                    "agent_id": agent_id,
                    "provider_id": provider_id,
                    "tool": "publish_message",
                },
            )
        if trace:
            record_tool_result(
                self.repository,
                mission_id,
                turn_id,
                agent_id,
                "publish_message",
                summary="Published message {id}".format(id=message.id),
                provider_id=provider_id,
                output=content,
            )
        return message

    @roundtable_guard
    def write_artifact(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        relative_path: str,
        content: str,
        *,
        provider_id: Optional[str] = None,
    ) -> Path:
        self._require_mission(mission_id)
        record_tool_call(
            self.repository,
            mission_id,
            turn_id,
            agent_id,
            "write_artifact",
            {"relative_path": relative_path},
            provider_id,
        )
        mission_dir = (self.artifact_root / mission_id).resolve()
        target = (mission_dir / relative_path).resolve()
        try:
            normalized_relative_path = target.relative_to(mission_dir).as_posix()
        except ValueError:
            record_tool_error(
                self.repository,
                mission_id,
                turn_id,
                agent_id,
                "write_artifact",
                "Artifact path is outside artifact directory",
                provider_id,
            )
            raise MCPToolError("Artifact path is outside artifact directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        artifact_id = "{mission_id}:{path}".format(
            mission_id=mission_id,
            path=normalized_relative_path,
        )
        self.repository.add_event(
            mission_id,
            "artifact",
            {
                "mission_id": mission_id,
                "agent_id": agent_id,
                "turn_id": turn_id,
                "title": target.name,
                "path": normalized_relative_path,
            },
        )
        record_tool_result(
            self.repository,
            mission_id,
            turn_id,
            agent_id,
            "write_artifact",
            summary="Wrote {path}".format(path=normalized_relative_path),
            artifact_id=artifact_id,
            provider_id=provider_id,
            output="{path} · {n} chars · sha256:{digest}".format(
                path=normalized_relative_path, n=len(content), digest=digest),
        )
        return target

    READ_ARTIFACT_MAX_CHARS = 20000

    def read_discussion_result(self, mission_id, agent_id, turn_id, provider_id, source_id, offset=0, max_chars=8000):
        from backend.app.orchestration.deliberation import Deliberation
        try:
            result = Deliberation(self.repository).read_entry(mission_id, agent_id, turn_id, provider_id,
                self.lease_token, source_id, offset, max_chars)
            record_tool_call(self.repository, mission_id, turn_id, agent_id, 'read_discussion_result',
                             {'source_id': source_id, 'offset': offset}, provider_id)
            page = result.get('text') if isinstance(result, dict) else None
            record_tool_result(self.repository, mission_id, turn_id, agent_id, 'read_discussion_result',
                               summary='Returned frozen source page', provider_id=provider_id,
                               output=page if isinstance(page, str) else None)
            return result
        except ValueError as exc:
            raise MCPToolError(str(exc)) from exc

    def submit_discussion_result(self, mission_id, agent_id, turn_id, provider_id, result):
        from backend.app.orchestration.deliberation import Deliberation
        kind = result.get('kind') if isinstance(result, dict) else 'invalid'
        if not isinstance(kind, str) or kind not in {'opinion', 'contribution', 'review', 'draft'}:
            kind = 'invalid'
        record_tool_call(self.repository, mission_id, turn_id, agent_id, 'submit_discussion_result', {'kind': kind}, provider_id)
        try:
            submitted = Deliberation(self.repository).submit(mission_id, agent_id, turn_id, provider_id, self.lease_token, result)
            record_tool_result(self.repository, mission_id, turn_id, agent_id, 'submit_discussion_result', summary='Structured result accepted', provider_id=provider_id)
            return submitted
        except ValueError as exc:
            raise MCPToolError(str(exc)) from exc

    def read_artifact(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        relative_path: str,
        *,
        provider_id: Optional[str] = None,
        with_line_numbers: bool = True,
        source_mission_id: Optional[str] = None,
        offset: int = 0,
        max_chars: int = 8000,
    ) -> Dict[str, object]:
        from backend.app.orchestration.deliberation import is_v2
        if is_v2(self.repository.get_mission(mission_id)):
            from backend.app.orchestration.deliberation import Deliberation
            key = f'artifact:{source_mission_id or mission_id}:{relative_path}'
            record_tool_call(
                self.repository, mission_id, turn_id, agent_id, "read_artifact",
                {"relative_path": relative_path, "offset": offset}, provider_id,
            )
            try:
                page = Deliberation(self.repository).read_material(
                    mission_id, agent_id, turn_id, provider_id, self.lease_token, key, offset, max_chars)
            except ValueError as exc:
                record_tool_error(self.repository, mission_id, turn_id, agent_id,
                                  "read_artifact", str(exc), provider_id)
                raise MCPToolError(str(exc)) from exc
            content = page.get("content") if isinstance(page.get("content"), str) else ""
            record_tool_result(
                self.repository, mission_id, turn_id, agent_id, "read_artifact",
                summary="Read frozen material {p}".format(p=relative_path),
                provider_id=provider_id, output=content,
            )
            return page
        self._require_mission(mission_id)
        record_tool_call(
            self.repository,
            mission_id,
            turn_id,
            agent_id,
            "read_artifact",
            {"relative_path": relative_path, "with_line_numbers": with_line_numbers,
             "source_mission_id": source_mission_id},
            provider_id,
        )
        read_mission_id = mission_id
        if source_mission_id is not None:
            if not isinstance(source_mission_id, str) or not source_mission_id.strip():
                record_tool_error(self.repository, mission_id, turn_id, agent_id,
                                  "read_artifact", "source_mission_id must be a non-empty string", provider_id)
                raise MCPToolError("source_mission_id must be a non-empty string")
            read_mission_id = source_mission_id.strip()
            source = self.repository.get_mission(read_mission_id)
            current = self.repository.get_mission(mission_id)
            if source is None:
                record_tool_error(self.repository, mission_id, turn_id, agent_id,
                                  "read_artifact", f"Unknown source mission: {read_mission_id}", provider_id)
                raise MCPToolError(f"Unknown source mission: {read_mission_id}")
            if read_mission_id != mission_id and (
                current is None or not current.conversation_id
                or current.conversation_id != source.conversation_id
            ):
                record_tool_error(self.repository, mission_id, turn_id, agent_id,
                                  "read_artifact", "source mission is in a different conversation", provider_id)
                raise MCPToolError("source mission is in a different conversation")
        mission_dir = (self.artifact_root / read_mission_id).resolve()
        try:
            target = (mission_dir / relative_path).resolve()
            normalized = target.relative_to(mission_dir).as_posix()
        except ValueError:
            record_tool_error(
                self.repository,
                mission_id,
                turn_id,
                agent_id,
                "read_artifact",
                "Artifact path is outside artifact directory",
                provider_id,
            )
            raise MCPToolError("Artifact path is outside artifact directory")
        if not target.exists() or not target.is_file():
            record_tool_error(
                self.repository,
                mission_id,
                turn_id,
                agent_id,
                "read_artifact",
                "Artifact not found: {p}".format(p=normalized),
                provider_id,
            )
            raise MCPToolError("Artifact not found: {p}".format(p=normalized))
        raw = target.read_text(encoding="utf-8", errors="replace")
        total_chars = len(raw)
        truncated = total_chars > self.READ_ARTIFACT_MAX_CHARS
        body = raw[: self.READ_ARTIFACT_MAX_CHARS] if truncated else raw
        if with_line_numbers:
            lines = body.splitlines()
            rendered = "\n".join(
                "{n:>5}\t{line}".format(n=i + 1, line=line)
                for i, line in enumerate(lines)
            )
        else:
            rendered = body
        result: Dict[str, object] = {
            "mission_id": read_mission_id,
            "path": normalized,
            "content": rendered,
            "total_chars": total_chars,
            "returned_chars": len(body),
            "truncated": truncated,
        }
        record_tool_result(
            self.repository,
            mission_id,
            turn_id,
            agent_id,
            "read_artifact",
            summary="Read {p} ({n}/{t} chars{trunc})".format(
                p=normalized,
                n=len(body),
                t=total_chars,
                trunc=", truncated" if truncated else "",
            ),
            provider_id=provider_id,
            output=body,
        )
        return result

    @roundtable_guard
    def log_thought(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: Optional[str],
        content: str,
        provider_id: Optional[str] = None,
        *,
        kind: Optional[str] = None,
        tags: Optional[List[str]] = None,
        trace: bool = True,
    ) -> Dict[str, object]:
        self._require_mission(mission_id)
        normalized_turn_id = (
            turn_id.strip()
            if isinstance(turn_id, str) and turn_id.strip()
            else "unknown"
        )
        normalized_kind = kind or "reasoning"
        valid_kinds = {"reasoning", "plan", "doubt", "decision"}
        if normalized_kind not in valid_kinds:
            valid_kind_list = ", ".join(sorted(valid_kinds))
            if trace:
                record_tool_error(
                    self.repository,
                    mission_id,
                    normalized_turn_id,
                    agent_id,
                    "log_thought",
                    "kind must be one of: {kinds}".format(kinds=valid_kind_list),
                    provider_id,
                )
            raise MCPToolError(
                "log_thought 'kind' must be one of: {kinds}".format(
                    kinds=valid_kind_list
                )
            )
        if not isinstance(content, str) or not content.strip():
            if trace:
                record_tool_error(
                    self.repository,
                    mission_id,
                    normalized_turn_id,
                    agent_id,
                    "log_thought",
                    "content is required",
                    provider_id,
                )
            raise MCPToolError("log_thought requires non-empty 'content'")
        normalized_tags: List[str] = []
        if tags is not None:
            if not isinstance(tags, list) or any(
                not isinstance(item, str) or not item.strip() for item in tags
            ):
                if trace:
                    record_tool_error(
                        self.repository,
                        mission_id,
                        normalized_turn_id,
                        agent_id,
                        "log_thought",
                        "tags must be a list of non-empty strings",
                        provider_id,
                    )
                raise MCPToolError(
                    "log_thought 'tags' must be a list of non-empty strings"
                )
            normalized_tags = [tag.strip() for tag in tags]
        if trace:
            record_tool_call(
                self.repository,
                mission_id,
                normalized_turn_id,
                agent_id,
                "log_thought",
                {
                    "content_length": len(content),
                    "kind": normalized_kind,
                    "tags": normalized_tags,
                },
                provider_id,
            )
        event_id = self.repository.add_event(
            mission_id,
            "agent.thought",
            {
                "mission_id": mission_id,
                "agent_id": agent_id,
                "turn_id": normalized_turn_id,
                "provider_id": provider_id,
                "kind": normalized_kind,
                "content": content,
                "tags": normalized_tags,
            },
        )
        if trace:
            record_tool_result(
                self.repository,
                mission_id,
                normalized_turn_id,
                agent_id,
                "log_thought",
                summary="Logged thought ({n} chars{tag_note})".format(
                    n=len(content),
                    tag_note=", tags=" + ",".join(normalized_tags) if normalized_tags else "",
                ),
                provider_id=provider_id,
                output=content,
            )
        return {"ok": True, "event_id": event_id}

    @roundtable_guard
    def update_status(
        self,
        mission_id: str,
        status: str,
        summary: Optional[str] = None,
        *,
        agent_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        provider_id: Optional[str] = None,
    ) -> None:
        mission = self._require_mission(mission_id)
        if agent_id and turn_id:
            record_tool_call(
                self.repository,
                mission_id,
                turn_id,
                agent_id,
                "update_status",
                {"status": status},
                provider_id,
            )
        payload = {"mission_id": mission.id, "status": status, "summary": summary}
        self.repository.add_event(mission_id, "status", payload)
        if agent_id and turn_id:
            record_tool_result(
                self.repository,
                mission_id,
                turn_id,
                agent_id,
                "update_status",
                summary="Status updated to {status}".format(status=status),
                provider_id=provider_id,
                output=summary or "",
            )

    @roundtable_guard
    def invoke_agent(
        self,
        mission_id: str,
        sender_agent_id: str,
        sender_turn_id: str,
        target_agent_id: str,
        purpose: str,
        provider_id: str,
        *,
        mode: str = "fire_and_forget",
    ) -> Dict[str, int]:
        mission = self._require_mission(mission_id)
        if mission.room_mode == 'roundtable':
            from backend.app.orchestration.roundtable import Roundtable
            record_tool_call(self.repository, mission_id, sender_turn_id, sender_agent_id, 'invoke_agent', {'target_agent_id': target_agent_id, 'purpose': purpose, 'mode': mode}, provider_id)
            result = Roundtable(self.repository).invoke(mission_id, sender_agent_id, sender_turn_id, target_agent_id, purpose, mode)
            record_tool_result(self.repository, mission_id, sender_turn_id, sender_agent_id, 'invoke_agent', summary=result['status'], provider_id=provider_id)
            return result
        self._require_mission(mission_id)
        record_tool_call(
            self.repository,
            mission_id,
            sender_turn_id,
            sender_agent_id,
            "invoke_agent",
            {
                "target_agent_id": target_agent_id,
                "purpose": purpose,
                "mode": mode,
            },
            provider_id,
        )
        try:
            if mode not in ("fire_and_forget", "wait_reply"):
                raise MCPToolError("mode {mode} not supported".format(mode=mode))
            if not isinstance(target_agent_id, str) or not target_agent_id.strip():
                raise MCPToolError("target_agent_id is required")
            if self.valid_agent_ids and target_agent_id not in self.valid_agent_ids:
                raise MCPToolError("unknown agent: {id}".format(id=target_agent_id))
            if target_agent_id == sender_agent_id:
                raise MCPToolError("agent cannot invoke itself")
            if mode == "wait_reply" and self._peek_resume_chain is not None:
                chain = self._peek_resume_chain(sender_agent_id)
                if target_agent_id in chain:
                    raise MCPToolError(
                        "a2a wait cycle: {path}→{target}".format(
                            path="→".join(chain + [sender_agent_id]),
                            target=target_agent_id,
                        )
                    )
            if not isinstance(purpose, str) or not purpose.strip():
                raise MCPToolError("purpose is required")

            if self.a2a_enqueue is None:
                # Subprocess path: enforce budget by counting existing a2a events in the DB.
                if self.max_a2a_invocations > 0:
                    existing = sum(
                        1
                        for e in self.repository.list_events_after(mission_id, 0)
                        if e.event_type in ("a2a.invoke", "a2a.request")
                    )
                    if existing >= self.max_a2a_invocations:
                        raise MCPToolError(
                            "a2a 深度耗尽 ({used}/{limit})".format(
                                used=existing,
                                limit=self.max_a2a_invocations,
                            )
                        )
                # Persist the request to the DB so the runner can pick it up
                # after this turn ends.
                record_a2a_request(
                    self.repository,
                    mission_id,
                    sender_agent_id,
                    sender_turn_id,
                    target_agent_id,
                    purpose.strip(),
                    mode,
                )
                queued_position = sum(
                    1
                    for e in self.repository.list_events_after(mission_id, 0)
                    if e.event_type == "a2a.request"
                    and e.payload.get("sender_turn_id") == sender_turn_id
                )
                record_tool_result(
                    self.repository,
                    mission_id,
                    sender_turn_id,
                    sender_agent_id,
                    "invoke_agent",
                    summary="Persisted a2a.request for {target} at position {pos}".format(
                        target=target_agent_id,
                        pos=queued_position,
                    ),
                    provider_id=provider_id,
                )
                # Count all mission-level a2a events (invoke + request) to compute
                # remaining budget accurately, including events from prior turns.
                total_used = sum(
                    1
                    for e in self.repository.list_events_after(mission_id, 0)
                    if e.event_type in ("a2a.invoke", "a2a.request")
                )
                remaining = (
                    max(0, self.max_a2a_invocations - total_used)
                    if self.max_a2a_invocations > 0
                    else -1
                )
                result = {"queued_position": queued_position, "estimated_remaining_a2a_budget": remaining}
                if mode == "wait_reply":
                    result["reply_pending"] = True
                    result["resume_position"] = queued_position + 1
                return result

            # In-process path: absorb a redundant fire-and-forget invoke when the
            # target already has a scheduled turn in the remaining workflow. Queuing
            # an extra a2a turn for someone about to speak makes them speak twice on
            # the same topic ("漂移/重复发言"). wait_reply is never skipped — the
            # sender is blocked on a real reply.
            if (
                mode == "fire_and_forget"
                and self._is_target_scheduled is not None
                and self._is_target_scheduled(target_agent_id)
            ):
                record_a2a_skipped(
                    self.repository,
                    mission_id,
                    sender_agent_id,
                    sender_turn_id,
                    target_agent_id,
                    purpose.strip(),
                    reason="target already scheduled in workflow",
                )
                record_tool_result(
                    self.repository,
                    mission_id,
                    sender_turn_id,
                    sender_agent_id,
                    "invoke_agent",
                    summary="Skipped {target}: already scheduled to speak".format(
                        target=target_agent_id,
                    ),
                    provider_id=provider_id,
                )
                return {"queued_position": 0, "skipped": True}

            # In-process path: validate budget and enqueue immediately.
            used = self.a2a_budget_used()
            if used >= self.max_a2a_invocations:
                raise MCPToolError(
                    "a2a 深度耗尽 ({used}/{limit})".format(
                        used=used,
                        limit=self.max_a2a_invocations,
                    )
                )

            step = WorkflowStep(
                agent_id=target_agent_id,
                purpose=purpose.strip(),
                origin="a2a",
                sender_agent_id=sender_agent_id,
                sender_turn_id=sender_turn_id,
            )
            position = self.a2a_enqueue(step)
            queued_position = int(position) if position is not None else 1
            if mode == "wait_reply":
                resume_step = WorkflowStep(
                    agent_id=sender_agent_id,
                    purpose="承接 {target} 对『{purpose}』的回复".format(
                        target=target_agent_id, purpose=purpose.strip(),
                    ),
                    origin="a2a_resume",
                    sender_agent_id=sender_agent_id,
                    sender_turn_id=sender_turn_id,
                    parent_step_id=step.step_id,
                )
                self.a2a_enqueue(resume_step)
            record_a2a_invoke(
                self.repository,
                mission_id,
                sender_agent_id,
                sender_turn_id,
                target_agent_id,
                purpose.strip(),
                mode,
                queued_position,
            )
            remaining = max(0, self.max_a2a_invocations - used - 1)
            record_tool_result(
                self.repository,
                mission_id,
                sender_turn_id,
                sender_agent_id,
                "invoke_agent",
                summary="Queued {target} at position {position}".format(
                    target=target_agent_id,
                    position=queued_position,
                ),
                provider_id=provider_id,
            )
            result = {
                "queued_position": queued_position,
                "estimated_remaining_a2a_budget": remaining,
            }
            if mode == "wait_reply":
                result["reply_pending"] = True
                result["resume_position"] = queued_position + 1
            return result
        except MCPToolError as exc:
            record_tool_error(
                self.repository,
                mission_id,
                sender_turn_id,
                sender_agent_id,
                "invoke_agent",
                str(exc),
                provider_id,
            )
            raise

    def _require_mission(self, mission_id: str):
        mission = self.repository.get_mission(mission_id)
        if mission is None:
            raise MCPToolError(f"Unknown mission: {mission_id}")
        return mission
