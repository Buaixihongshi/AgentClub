"""Managed, token-protected draining; serialize it against incoming mutations."""
from __future__ import annotations

import asyncio
import hmac
import os
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse


def activity(repository) -> dict:
    # Counts only, never message bodies or credentials. Non-terminal discussions
    # and workers matter even when a mission is waiting for human input.
    with repository._transaction() as db:
        missions = db.execute("""SELECT count(*) FROM missions WHERE
            json_extract(data, '$.status') NOT IN ('completed','failed','completed_with_warnings')
            OR (json_extract(data, '$.discussion.protocol')='deliberation_v2'
                AND coalesce((SELECT json_extract(e.payload, '$.phase') FROM events e
                    WHERE e.mission_id=missions.id AND e.event_type='discussion.v2.state'
                    ORDER BY e.id DESC LIMIT 1), 'unknown')!='ended')""").fetchone()[0]
        maintenance = db.execute("""SELECT count(*) FROM maintenance_runs WHERE
            json_extract(data, '$.phase') NOT IN ('completed','blocked','failed','cancelled')""").fetchone()[0]
        grants = db.execute("SELECT count(*) FROM execution_grants WHERE state='active'").fetchone()[0]
    return {"active_missions": missions, "active_maintenance": maintenance, "active_grants": grants}


def install(app, repository, settings):
    app.state.draining = False
    app.state.drain_pending = False
    # Test clients and embedded callers may use different event loops. Admission
    # bookkeeping is thread-safe and never holds a lock across an await.
    lock = threading.Lock()
    in_flight = 0
    token = os.environ.get("YIREN_LIFECYCLE_TOKEN", "")
    router = APIRouter(prefix="/api/runtime", tags=["runtime"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        nonlocal in_flight
        if request.method in {"GET", "HEAD", "OPTIONS"} or request.url.path in {"/api/runtime/drain", "/api/runtime/resume"}:
            return await call_next(request)
        with lock:
            if app.state.draining or app.state.drain_pending:
                return JSONResponse(status_code=503, content={"detail": "服务正在切换版本，请稍后重试"}, headers={"Retry-After": "5"})
            if settings.demo_only and (request.url.path.endswith("/verify") or
                    (request.url.path.endswith("/probe") and "/fake/" not in request.url.path)):
                return JSONResponse(status_code=409, content={"detail": "演示环境不调用真实 Provider；请显式启动 --real-cli 环境"})
            in_flight += 1
        try:
            return await call_next(request)
        finally:
            with lock: in_flight -= 1

    def authenticate(request):
        if not token or not hmac.compare_digest(request.headers.get("X-AgentClub-Control", ""), token):
            raise HTTPException(403, "Managed runtime control requires its private token")

    @router.post("/drain")
    async def drain(request: Request):
        authenticate(request)
        with lock:
            if app.state.drain_pending: raise HTTPException(409, "Another drain is in progress")
            app.state.drain_pending = True
        try:
            deadline = time.monotonic() + 10
            while True:
                with lock: pending = in_flight
                if not pending: break
                if time.monotonic() > deadline: raise HTTPException(409, "A write request is still in flight")
                await asyncio.sleep(0.01)
            counts = activity(repository)
            if any(counts.values()):
                raise HTTPException(409, {"message": "服务仍有未完成工作", **counts})
            with lock: app.state.draining = True
            return {"draining": True, **counts}
        finally:
            with lock: app.state.drain_pending = False

    @router.post("/resume")
    async def resume(request: Request):
        authenticate(request)
        with lock:
            if app.state.drain_pending: raise HTTPException(409, "Drain is in progress")
            app.state.draining = False
        return {"draining": False}

    app.include_router(router)
