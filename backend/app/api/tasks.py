import mimetypes
import threading

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from backend.app.settings import snapshot_settings
from backend.app.work.models import WorkCommand
from backend.app.work.service import WorkError
from backend.app.work.tasks import CreateTask, Tasks


def build_router(settings, repository, adapter=None):
    router = APIRouter(prefix='/api/tasks', tags=['tasks'])

    def service():
        return Tasks(repository, check_timeout=settings.cli_timeout)

    def execute(action):
        try:
            return action()
        except WorkError as exc:
            return JSONResponse(status_code=exc.status_code, content={'detail': str(exc)})
        except ValidationError as exc:
            # No request body or private execution details in errors.
            messages = '; '.join(e['msg'] for e in exc.errors(include_input=False))
            return JSONResponse(status_code=422, content={'detail': messages})

    def schedule(result):
        if isinstance(result, dict) and not result['task']['paused'] and result['task']['status'] not in {'done', 'cancelled'}:
            from backend.app.api.shared_work import drain
            threading.Thread(target=drain, args=(repository, snapshot_settings(settings),
                             result['execution_conversation_id'], adapter), daemon=True).start()
        return result

    @router.get('')
    def listing():
        return execute(lambda: {'tasks': service().list()})

    @router.post('', status_code=201)
    def create(request: CreateTask):
        return schedule(execute(lambda: service().create(request)))

    @router.get('/{task_id}')
    def detail(task_id: str):
        return execute(lambda: service().detail(task_id))

    @router.post('/{task_id}/commands')
    def command(task_id: str, request: WorkCommand):
        return schedule(execute(lambda: service().command(task_id, request)))

    @router.get('/{task_id}/logs/{log_id}')
    def log(task_id: str, log_id: str):
        def read():
            return Response(service().read_log(task_id, log_id), media_type='text/plain')
        return execute(read)

    @router.get('/{task_id}/snapshots/{snapshot_id}/file')
    def file(task_id: str, snapshot_id: str, path: str):
        def read():
            from backend.app.work.snapshots import content
            context = service().evidence(task_id, snapshot_id)
            return Response(content(repository, context, snapshot_id, path),
                            media_type=mimetypes.guess_type(path)[0] or 'application/octet-stream', headers={
                                'X-Content-Type-Options': 'nosniff',
                                'Content-Security-Policy': "sandbox allow-scripts allow-forms; default-src 'none'; form-action 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:",
                            })
        return execute(read)

    return router
