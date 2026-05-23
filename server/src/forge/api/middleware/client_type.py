"""请求客户端类型中间件。

ContextVar 声明在 forge.core.request_context, 本模块只负责 HTTP 层识别和赋值。
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from forge.core.request_context import (
    CLIENT_TYPE_HEADER,
    ClientType,
    _CLIENT_TYPE,
    infer_client_type,
)

__all__ = [
    "ClientTypeMiddleware",
]


class ClientTypeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        client_type = infer_client_type(
            request.headers.get(CLIENT_TYPE_HEADER),
            request.headers.get("user-agent"),
        )
        token = _CLIENT_TYPE.set(client_type)
        request.state.client_type = client_type
        try:
            response = await call_next(request)
        finally:
            _CLIENT_TYPE.reset(token)
        response.headers[CLIENT_TYPE_HEADER] = client_type
        return response
