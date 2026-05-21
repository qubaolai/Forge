"""请求客户端类型中间件 (S6.5 M4).

本期只识别并透传 ``client_type`` 元数据,不改变任何业务行为.
后续 SaaS 化时,工具访问策略会基于 ``deployment_mode × client_type`` 收紧.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Literal, cast

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

ClientType = Literal["cli", "web"]

CLIENT_TYPE_HEADER = "X-Client-Type"
_CLIENT_TYPES: frozenset[str] = frozenset({"cli", "web"})
_CLIENT_TYPE: ContextVar[ClientType] = ContextVar("client_type", default="cli")


def current_client_type() -> ClientType:
    """返回当前请求的 client_type,无请求上下文时默认 ``cli``."""
    return _CLIENT_TYPE.get()


def set_client_type(client_type: str):
    """显式设置 client_type,返回 ContextVar token 供调用方按需 reset."""
    return _CLIENT_TYPE.set(_normalize_client_type(client_type))


def infer_client_type(header_value: str | None, user_agent: str | None = None) -> ClientType:
    """从请求 header 或 user-agent 推断客户端类型.

    显式 ``X-Client-Type`` 优先;没有 header 时,常见浏览器 UA 推断为 web,
    其余默认 cli,保持本地单机模式宽松兼容.
    """
    if header_value:
        return _normalize_client_type(header_value)

    ua = (user_agent or "").lower()
    if any(mark in ua for mark in ("mozilla/", "chrome/", "safari/", "firefox/")):
        return "web"
    return "cli"


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


def _normalize_client_type(value: str) -> ClientType:
    raw = (value or "").strip().lower()
    if raw in _CLIENT_TYPES:
        return cast(ClientType, raw)
    return "cli"


__all__ = [
    "CLIENT_TYPE_HEADER",
    "ClientType",
    "ClientTypeMiddleware",
    "_CLIENT_TYPE",
    "current_client_type",
    "infer_client_type",
    "set_client_type",
]
