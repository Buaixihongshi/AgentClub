import logging
import threading
import mimetypes

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from backend.app.settings import EXTENSION_DISABLED, snapshot_settings
from backend.app.extensions.tasks.models import WorkCommand
from backend.app.extensions.tasks.service import SharedWork, WorkError

logger = logging.getLogger(__name__)


def drain(repository, settings, conversation_id, adapter=None):
    """Claims commit before invocation; multiple dispatch threads cannot duplicate a stage."""
    from backend.app.api.missions import _run_mission_in_background
    if not settings.tasks_enabled:
        # Saved work stays paused in place and resumes once tasks are re-enabled.
        return
    work = SharedWork(repository, check_timeout=settings.cli_timeout)
    if work.read(conversation_id).get('protocol') == 'adaptive_v1':
        from backend.app.extensions.tasks.adaptive import AdaptiveWork
        from backend.app.extensions.tasks.releases import process
        process(AdaptiveWork(repository, check_timeout=settings.cli_timeout), conversation_id)
    adaptive = SharedWork(repository).read(conversation_id).get('protocol') == 'adaptive_v1'
    for _ in range(200 if adaptive else 12):
        with repository._transaction() as db:
            waiting = [row['conversation_id'] for row in db.execute('SELECT conversation_id FROM work_boards').fetchall()]
        mission = None
        for candidate in [conversation_id, *[c for c in waiting if c != conversation_id]]:
            mission = work.claim(candidate)
            if mission:
                break
        if mission is None:
            return
        try:
            _run_mission_in_background(repository, settings, mission.id, adapter)
        finally:
            work.finish(mission.conversation_id, mission.id)
    # A finite dispatcher budget must leave an explicit paused checkpoint.
    for candidate in waiting:
        board = work.read(candidate)
        if board['enabled'] and board.get('protocol') != 'adaptive_v1':
            try:
                work.command(candidate, 'pause', {'reason': '本轮已运行 12 个阶段，请检查进度后继续启动'}, board['revision'])
            except WorkError:
                logger.info('Shared work changed at dispatch budget boundary')


def pump(repository, settings, stopped, adapter=None):
    """Notifications are hints; scanning durable ready records closes lost-wake gaps."""
    while not stopped.is_set():
        try:
            with repository._transaction() as db:
                ids = [row['conversation_id'] for row in db.execute(
                    "SELECT conversation_id FROM work_boards WHERE json_extract(data, '$.protocol')='adaptive_v1'")]
            for conversation_id in ids:
                if stopped.is_set():
                    return
                drain(repository, snapshot_settings(settings), conversation_id, adapter)
        except Exception:
            logger.exception('Durable work dispatcher failed; saved work remains recoverable')
        stopped.wait(1)


def build_router(settings, repository, adapter=None):
    router = APIRouter(prefix='/api/conversations/{conversation_id}/work', tags=['shared-work'])

    @router.get('')
    def read(conversation_id: str):
        try:
            return SharedWork(repository).read(conversation_id)
        except WorkError as exc:
            return JSONResponse(status_code=exc.status_code, content={'detail': str(exc)})

    @router.get('/logs/{log_id}')
    def log(conversation_id: str, log_id: str):
        try:
            return Response(SharedWork(repository).read_log(conversation_id, log_id), media_type='text/plain')
        except WorkError as exc:
            return JSONResponse(status_code=exc.status_code, content={'detail': str(exc)})

    @router.get('/snapshots/{snapshot_id}/file')
    def snapshot_file(conversation_id: str, snapshot_id: str, path: str):
        from backend.app.extensions.tasks.snapshots import content
        try:
            raw = content(repository, conversation_id, snapshot_id, path)
            media = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            return Response(raw, media_type=media, headers={
                'X-Content-Type-Options': 'nosniff',
                'Content-Security-Policy': "sandbox allow-scripts allow-forms; default-src 'none'; form-action 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:",
            })
        except WorkError as exc:
            return JSONResponse(status_code=exc.status_code, content={'detail': str(exc)})

    @router.post('')
    def command(conversation_id: str, request: WorkCommand):
        if not snapshot_settings(settings).tasks_enabled:
            return JSONResponse(status_code=409, content={'detail': EXTENSION_DISABLED['tasks']})
        try:
            result = SharedWork(repository).command(conversation_id, request.action, request.data, request.expected_revision,
                                                  operation_id=request.operation_id)
        except WorkError as exc:
            return JSONResponse(status_code=exc.status_code, content={'detail': str(exc)})
        except ValidationError:
            return JSONResponse(status_code=422, content={'detail': '开发约定或任务字段不完整，请检查验收项与验证命令'})
        if (request.action in {'start', 'retry'} or result.get('protocol') == 'adaptive_v1') and result['enabled']:
            threading.Thread(target=drain, args=(repository, snapshot_settings(settings), conversation_id, adapter), daemon=True).start()
        return result

    return router
