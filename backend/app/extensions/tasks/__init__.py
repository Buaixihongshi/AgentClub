"""Tasks extension: durable shared work, its workspaces, previews and releases."""


def install():
    from backend.app import extension_points as ep
    from backend.app.extensions.tasks import hooks, tools
    for name in ('mission_held', 'mission_takeover', 'mission_finished', 'mission_created',
                 'archive_allowed', 'attempt_check', 'demo_turn', 'route_turn', 'prompt_fragments'):
        ep.register(name, getattr(hooks, name))
    ep.register('tool_check', tools.tool_check)
    ep.register('tool_override', tools.override)
    ep.register('context_extras', tools.context_extras)
    ep.register('mcp_tools_list', tools.mcp_tools_list)
    ep.tool_methods['shared_work'] = tools.shared_work
    ep.mcp_handlers.update(tools.MCP_HANDLERS)
    if tools.SHARED_WORK_DESCRIPTOR not in ep.mcp_descriptors:
        ep.mcp_descriptors.append(tools.SHARED_WORK_DESCRIPTOR)


def recover(repository, settings):
    from backend.app.extensions.tasks.service import SharedWork
    SharedWork(repository).recover()


def routers(settings, repository, adapter):
    from backend.app.extensions.tasks.api.shared_work import build_router as build_work_router
    from backend.app.extensions.tasks.api.tasks import build_router as build_tasks_router
    return [build_work_router(settings, repository, adapter), build_tasks_router(settings, repository, adapter)]


def background_loop(repository, settings, stopped, adapter):
    from backend.app.extensions.tasks.api.shared_work import pump
    pump(repository, settings, stopped, adapter)
