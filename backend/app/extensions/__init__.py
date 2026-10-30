"""Optional extensions (discussion, tasks, maintenance), composed by main.py.

The core never imports this package; it reaches extensions only through the
hooks in ``backend.app.extension_points``. Installing is idempotent and does
not enable anything: the runtime switches stay in Settings
(``discussion_enabled`` / ``tasks_enabled`` / ``maintenance_enabled``).
"""
from backend.app.extensions import discussion, maintenance, tasks


def install():
    from backend.app import extension_points
    # Tasks first: an adaptive-work mission is taken over before discussion checks.
    tasks.install()
    discussion.install()
    extension_points.installed(__name__)


def recover(repository, settings):
    discussion.recover(repository, settings)
    tasks.recover(repository, settings)
    maintenance.recover(repository, settings)


def background_loops():
    return [tasks.background_loop]


def maintenance_router(settings, repository):
    return maintenance.router(settings, repository)


def mission_routers(settings, repository, adapter):
    return [*discussion.routers(settings, repository, adapter), *tasks.routers(settings, repository, adapter)]


def resume(repository, settings, adapter):
    discussion.resume(repository, settings, adapter)
