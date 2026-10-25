"""User-facing management entry for long-term memory, checkpoints and handovers."""
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.app.domain.agent_registry import get_agent_registry
from backend.app.memory import checkpoints as checkpoint_store
from backend.app.memory import handover as handover_store
from backend.app.memory.store import MemoryError, MemoryStore
from backend.app.settings import Settings
from backend.app.storage.repository import Repository


class EntryUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    tags: Optional[list[str]] = None


def build_router(settings: Settings, repository: Repository) -> APIRouter:
    router = APIRouter(prefix='/api/memory', tags=['memory'])

    def store() -> MemoryStore:
        return MemoryStore(repository, settings.memory_root)

    def _error(exc):
        return JSONResponse(status_code=getattr(exc, 'status_code', 422), content={'detail': str(exc)})

    @router.get('/overview')
    def overview():
        registry = get_agent_registry(settings.config_dir)
        status = store().status()
        agents = []
        for agent in registry.all():
            entries = store().read_entries(agent.id, include_forgotten=True)
            agents.append({
                'id': agent.id, 'display_name': agent.display_name, 'role': agent.role,
                'active': sum(1 for e in entries if e['status'] == 'active'),
                'forgotten': sum(1 for e in entries if e['status'] == 'forgotten'),
            })
        return {'memory_root': str(settings.memory_root), 'consistency': status, 'agents': agents}

    @router.get('/agents/{agent_id}/entries')
    def agent_entries(agent_id: str, include_forgotten: bool = False):
        try:
            return {'entries': store().read_entries(agent_id, include_forgotten=include_forgotten)}
        except MemoryError as exc:
            return _error(exc)

    @router.get('/entries/{entry_id}')
    def read_entry(entry_id: str):
        try:
            return store().read_entry(entry_id)
        except MemoryError as exc:
            return _error(exc)

    @router.put('/entries/{entry_id}')
    def update_entry(entry_id: str, update: EntryUpdate):
        try:
            entry = store().revise(entry_id, title=update.title, body=update.body, tags=update.tags,
                                   editor='user')
            return {'id': entry['id'], 'revision': entry['revision'], 'updated_at': entry['updated_at']}
        except MemoryError as exc:
            return _error(exc)

    @router.post('/entries/{entry_id}/forget')
    def forget_entry(entry_id: str):
        try:
            entry = store().set_status(entry_id, 'forgotten')
            return {'id': entry['id'], 'status': entry['status']}
        except MemoryError as exc:
            return _error(exc)

    @router.post('/entries/{entry_id}/restore')
    def restore_entry(entry_id: str):
        try:
            entry = store().set_status(entry_id, 'active')
            return {'id': entry['id'], 'status': entry['status']}
        except MemoryError as exc:
            return _error(exc)

    @router.delete('/entries/{entry_id}')
    def purge_entry(entry_id: str):
        try:
            return store().purge(entry_id)
        except MemoryError as exc:
            return _error(exc)

    @router.post('/rebuild')
    def rebuild():
        return store().rebuild()

    @router.post('/sync')
    def sync():
        return store().sync(apply=True)

    @router.get('/conversations/{conversation_id}/checkpoints')
    def conversation_checkpoints(conversation_id: str):
        return {'checkpoints': checkpoint_store.list_for_conversation(repository, conversation_id)}

    @router.delete('/conversations/{conversation_id}/checkpoints')
    def clear_checkpoints(conversation_id: str, agent_id: Optional[str] = None):
        removed = checkpoint_store.clear(repository, conversation_id, agent_id)
        return {'removed': removed}

    @router.get('/handovers')
    def handovers(agent_id: Optional[str] = None, limit: int = 20):
        return {'handovers': handover_store.list_recent(repository, agent_id, limit)}

    return router
