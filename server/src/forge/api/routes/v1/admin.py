"""管理后台 — SSE 事件流。模型/供应商的管理接口见 models.py 和 providers.py。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from forge.api.dependencies import AdminUser
from forge.core.response import success
from forge.infrastructure.event_bus import get_event_bus

logger = logging.getLogger(__name__)

router = APIRouter()
_EVENT = "model_config_changed"


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
