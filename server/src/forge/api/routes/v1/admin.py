"""管理后台 — SSE 事件流。模型/供应商的管理接口见 models.py 和 providers.py。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from forge.api.dependencies import AdminUser, DbSession
from forge.api.schemas.admin import SystemModelBindingUpdateIn
from forge.core.exceptions import BadRequest
from forge.core.response import success
from forge.infrastructure.event_bus import get_event_bus

logger = logging.getLogger(__name__)

router = APIRouter()
_EVENT = "model_config_changed"


@router.get("/admin/model-bindings", tags=["admin:models"])
async def list_model_bindings(admin: AdminUser, db: DbSession):
    from forge.api.services.system_model_binding_service import SystemModelBindingService

    return success(await SystemModelBindingService(db).list_bindings())


@router.put("/admin/model-bindings/{role}", tags=["admin:models"])
async def update_model_binding(
    role: str,
    body: SystemModelBindingUpdateIn,
    admin: AdminUser,
    db: DbSession,
):
    from forge.api.services.system_model_binding_service import SystemModelBindingService
    from forge.retrieval.bound_model_resolver import get_bound_model_resolver

    try:
        result = await SystemModelBindingService(db).set_binding(
            role, body.model_id, updated_by=str(admin.id)
        )
    except ValueError as exc:
        raise BadRequest(str(exc), code=40070) from exc
    await db.commit()
    get_bound_model_resolver().clear()
    await get_event_bus().publish(_EVENT, {
        "type": "system_model_binding_changed",
        "role": role,
        **result,
        "timestamp": datetime.utcnow().isoformat(),
    })
    return success(result)


@router.get("/admin/rag-index/status", tags=["admin:models"])
async def rag_index_status(admin: AdminUser, db: DbSession):
    from forge.api.services.rag_index_service import RagIndexService

    return success(await RagIndexService(db).status())


@router.post("/admin/rag-index/rebuild", tags=["admin:models"])
async def rebuild_rag_index(admin: AdminUser, db: DbSession):
    from forge.api.services.rag_index_service import RagIndexService
    from forge.infrastructure.queue import get_task_queue

    try:
        job = await RagIndexService(db).create_rebuild(created_by=str(admin.id))
    except ValueError as exc:
        raise BadRequest(str(exc), code=40071) from exc
    await db.commit()
    get_task_queue().submit("rag.index.rebuild", job_id=job["id"])
    return success(job)


@router.post("/admin/rag-index/rebuild/{job_id}/retry", tags=["admin:models"])
async def retry_rag_index(job_id: str, admin: AdminUser, db: DbSession):
    from forge.api.services.rag_index_service import RagIndexService
    from forge.infrastructure.queue import get_task_queue

    try:
        job = await RagIndexService(db).retry(job_id, created_by=str(admin.id))
    except ValueError as exc:
        raise BadRequest(str(exc), code=40072) from exc
    await db.commit()
    get_task_queue().submit("rag.index.rebuild", job_id=job["id"])
    return success(job)


def _sse(event: dict) -> bytes:
    data = json.dumps(event, ensure_ascii=False, default=str)
    return f"event: {_EVENT}\ndata: {data}\n\n".encode()


@router.get("/admin/events", tags=["admin:events"])
async def admin_events(
    request: Request,
    admin: AdminUser,
    follow: bool = Query(False, description="长连接订阅模式"),
):
    """SSE 事件流：订阅模型配置变更通知。

    前端用法:
        const es = new EventSource("/api/v1/admin/events?follow=true");
        es.addEventListener("model_config_changed", (e) => {
            const data = JSON.parse(e.data);
            refreshModelList();
        });
    """
    if not follow:
        return success({"message": "使用 ?follow=true 订阅 SSE 事件流"})

    bus = get_event_bus()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    async def _handler(payload: dict) -> None:
        await queue.put(payload)

    unsubscribe = bus.subscribe(_EVENT, _handler)

    async def _stream():
        try:
            yield _sse({"type": "connected", "timestamp": datetime.utcnow().isoformat()})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield _sse(payload)
                except TimeoutError:
                    yield _sse({"type": "heartbeat", "timestamp": datetime.utcnow().isoformat()})
        finally:
            unsubscribe()

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
