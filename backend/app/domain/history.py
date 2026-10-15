"""Read-only dialogue projection shared by prompts, MCP and HTTP snapshots."""
from dataclasses import dataclass, replace
from datetime import timezone
from typing import Optional

from backend.app.domain.models import Message, MessageRole, Mission, MissionEvent


@dataclass(frozen=True)
class HistoryEntry:
    message: Message
    event_id: Optional[str]
    is_interruption: bool = False


@dataclass(frozen=True)
class MessageHistory:
    missions: list[Mission]
    stored_messages: list[Message]
    events: list[MissionEvent]

    def project(self, *, include_system: bool = False) -> list[HistoryEntry]:
        """Use event IDs for order and explicit links for identity, never text matching.

        Event-only interrupts are virtual user messages; reads never migrate rows.
        Historical rows without a message event follow the ordered timeline, with
        (created_at, id) as a deterministic fallback only for those rows.
        """
        messages = {message.id: message for message in self.stored_messages}
        missions = {mission.id: mission for mission in self.missions}
        entries: dict[str, HistoryEntry] = {}

        def append(message: Message, event_id: Optional[str], *, is_interruption: bool = False) -> None:
            previous = entries.get(message.id)
            if previous:
                if is_interruption:
                    entries[message.id] = replace(previous, is_interruption=True)
                return
            if include_system or message.role in (MessageRole.USER, MessageRole.AGENT):
                entries[message.id] = HistoryEntry(message, event_id, is_interruption)

        for event in self.events:
            if event.event_type == "message":
                message = messages.get(event.payload.get("id"))
                if message and message.mission_id == event.mission_id:
                    append(message, event.id)
            elif event.event_type == "interrupt":
                linked = messages.get(event.payload.get("message_id"))
                if linked and linked.mission_id == event.mission_id and linked.role == MessageRole.USER:
                    append(linked, event.id, is_interruption=True)
                    continue
                content = event.payload.get("content")
                if not isinstance(content, str):
                    continue
                mission = missions[event.mission_id]
                timestamp = event.created_at
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                append(Message(
                    id=f"interrupt:{event.id}", mission_id=event.mission_id,
                    conversation_id=mission.conversation_id, role=MessageRole.USER,
                    content=content, created_at=timestamp,
                ), event.id, is_interruption=True)

        for message in sorted(self.stored_messages, key=lambda m: (m.created_at, m.id)):
            append(message, None)
        return list(entries.values())


def prioritize_messages(entries: list[HistoryEntry], *, reply_to_turn_id: Optional[str] = None) -> list[Message]:
    """Keep the latest user input/interrupt, explicit reply, then recent speech."""
    messages = [entry.message for entry in entries]
    priority: list[Message] = []
    latest_user = next((m for m in reversed(messages) if m.role == MessageRole.USER), None)
    if latest_user:
        priority.append(latest_user)
    latest_interrupt = next((e.message for e in reversed(entries) if e.is_interruption), None)
    if latest_interrupt:
        priority.append(latest_interrupt)
    if reply_to_turn_id:
        priority.extend(m for m in reversed(messages) if m.turn_id == reply_to_turn_id)
    priority.extend(reversed(messages))
    return list({message.id: message for message in priority}.values())


def select_context_messages(entries: list[HistoryEntry], limit: int) -> list[Message]:
    selected = {m.id for m in prioritize_messages(entries)[:max(0, limit)]}
    return [entry.message for entry in entries if entry.message.id in selected]
