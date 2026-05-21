"""未捕获异常兜底中间件.

设计:
    - BusinessError / HTTPException / RequestValidationError 由
      core.exceptions.register_exception_handlers 处理 (走业务码 & 详细字段).
    - 这里只兜底 "完全未预期" 的异常: 记录 trace_id + 堆栈, 返回 500.
"""

from __future__ import annotations

import logging
import traceback

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .tracing import current_trace_id

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001
            trace_id = current_trace_id() or "-"
            logger.error(
                "unhandled exception trace_id=%s path=%s: %s\n%s",
                trace_id,
                request.url.path,
                exc,
                traceback.format_exc(),
            )
            return JSONResponse(
                status_code=500,
                content={
                    "code": 50000,
                    "message": "服务器内部错误",
                    "trace_id": trace_id,
                },
            )
