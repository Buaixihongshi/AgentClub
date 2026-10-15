"""One creation contract for the standalone and conversation endpoints."""
import json
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, StrictInt, model_validator

from backend.app.domain.agent_registry import get_agent_registry
from backend.app.domain.models import Conversation, DiscussionConfig, Message, MessageRole, Mission, utc_now
from backend.app.orchestration.roundtable import ACTIVE, DiscussionError


class DiscussionRequest(BaseModel):
    participant_ids: list[str] = Field(min_length=2, max_length=3)
    max_turns: Optional[StrictInt] = Field(default=None, ge=3, le=12)

    @model_validator(mode='after')
    def validate_participants(self):
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValueError('Discussion participants must be unique')
        if self.max_turns is not None and self.max_turns < len(self.participant_ids) + 1:
            raise ValueError('Reserve an opening turn for everyone and a summary turn')
        return self


class MissionRequest(BaseModel):
    goal: str
    title: Optional[str] = None
    target_agent_id: Optional[str] = None
    target_agent_ids: Optional[list[str]] = None
    room_mode: Literal['task', 'roundtable'] = 'task'
    discussion: Optional[DiscussionRequest] = None
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode='after')
    def validate_mode(self):
        if self.room_mode == 'roundtable':
            if self.discussion is None:
                raise ValueError('Roundtable requires discussion configuration')
            if self.target_agent_id is not None or self.target_agent_ids is not None:
                raise ValueError('Use discussion.participant_ids for roundtables')
            if not self.goal.strip():
                raise ValueError('Discussion goal must not be empty')
        elif self.discussion is not None:
            raise ValueError('Task mode does not accept discussion configuration')
        return self


class CreateMissionRequest(MissionRequest):
    conversation_id: Optional[str] = None


class DiscussionControlRequest(BaseModel):
    action: Literal['finish', 'stop']
    request_id: str = Field(min_length=1, max_length=128)
    summary_agent_id: Optional[str] = None


def ensure_available(repo, conversation):
    if conversation.status == 'archived':
        raise DiscussionError('Conversation is archived; restore it before creating a mission')
    if any(m.status in ACTIVE for m in repo.list_missions_for_conversation(conversation.id)):
        raise DiscussionError('Conversation already has an active mission')


def create_requested_mission(repo, settings, payload, conversation_id=None):
    scope = f'conversation:{conversation_id}' if conversation_id else 'new_conversation'
    fingerprint = payload.model_dump(exclude={'conversation_id'})
    with repo._transaction(immediate=True) as db:
        if payload.request_id:
            row = db.execute("SELECT mission_id, payload FROM events WHERE event_type = 'mission.request_accepted' AND json_extract(payload, '$.scope') = ? AND json_extract(payload, '$.request_id') = ?", (scope, payload.request_id)).fetchone()
            if row:
                if json.loads(row['payload'])['request'] != fingerprint:
                    raise DiscussionError('request_id was already used with different input')
                mission = repo.get_mission(row['mission_id'])
                return mission, repo.get_conversation(mission.conversation_id), False
        valid_ids = set(get_agent_registry(settings.config_dir).ids())
        targets = payload.target_agent_ids or ([payload.target_agent_id] if payload.target_agent_id else [])
        if any(t not in valid_ids for t in targets):
            raise DiscussionError('Invalid target_agent_id(s): ' + ', '.join(t for t in targets if t not in valid_ids), 400)
        config = None
        if payload.discussion:
            participants = payload.discussion.participant_ids
            if any(p not in valid_ids for p in participants):
                raise DiscussionError('Unknown discussion participant', 422)
            limit = payload.discussion.max_turns or len(participants) * 2 + 1
            if limit > settings.roundtable_max_turns:
                raise DiscussionError('Discussion exceeds server roundtable_max_turns', 422)
            config = DiscussionConfig(participant_ids=participants, max_turns=limit)
        if conversation_id:
            conversation = repo.get_conversation(conversation_id)
            if conversation is None:
                raise DiscussionError('Conversation not found', 404)
            ensure_available(repo, conversation)
        else:
            conversation = Conversation(id=f'c_{uuid4().hex[:12]}', title=payload.title or payload.goal[:40])
            repo.create_conversation(conversation)
        mission = Mission(id=f'm_{uuid4().hex}', title=payload.title or payload.goal, goal=payload.goal,
                          conversation_id=conversation.id, room_mode=payload.room_mode, discussion=config,
                          target_agent_id=targets[0] if targets else None, target_agent_ids=targets)
        repo.create_mission(mission)
        repo.add_message(Message(id=f'msg_{uuid4().hex}', mission_id=mission.id, conversation_id=conversation.id,
                                 role=MessageRole.USER, content=payload.goal))
        conversation = conversation.model_copy(update={'updated_at': utc_now()})
        repo.update_conversation(conversation)
        if payload.request_id:
            repo.add_event(mission.id, 'mission.request_accepted', {'scope': scope, 'request_id': payload.request_id, 'request': fingerprint})
        return mission, conversation, True
