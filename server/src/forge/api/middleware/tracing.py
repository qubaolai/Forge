"""请求上下文中间件 (trace_id + user_id).

为每个请求生成 trace_id (优先使用上游传入的 X-Request-ID),
并维护 user_id ContextVar 给业务层 (cost / audit / metrics) 用.

设计原则:
    - 不依赖 OpenTelemetry, 单独可用 (OTel 在 observability 层独立接入).
    - ContextVar 声明在 forge.core.request_context, 本模块只负责 HTTP 层赋值.
    - user_id 由 auth 完成后的入口点 (route / orchestrator / Celery task) 显式 set,
      因为 middleware 早于依赖解析, 此时还拿不到用户.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from forge.core.request_context import (
    REQUEST_ID_HEADER,
    _TRACE_ID,
)


class TracingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        trace_id = incoming if incoming else uuid.uuid4().hex

        token = _TRACE_ID.set(trace_id)
        request.state.trace_id = trace_id
        try:
            response = await call_next(request)
        finally:
            _TRACE_ID.reset(token)

        response.headers[REQUEST_ID_HEADER] = trace_id
        return response
