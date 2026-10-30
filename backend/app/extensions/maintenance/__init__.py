"""Durable host-owned repository maintenance primitives."""


def recover(repository, settings):
    from backend.app.extensions.maintenance.service import MaintenanceService
    MaintenanceService(repository, settings).recover_interrupted_runs()


def router(settings, repository):
    from backend.app.extensions.maintenance.api import build_router
    return build_router(settings, repository)
