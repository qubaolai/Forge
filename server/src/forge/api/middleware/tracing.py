"""请求上下文中间件 (trace_id + user_id).

为每个请求生成 trace_id (优先使用上游传入的 X-Request-ID),
并维护 user_id ContextVar 给业务层 (cost / audit / metrics) 用.

设计原则:
    - 不依赖 OpenTelemetry, 单独可用 (OTel 在 observability 层独立接入).
    - 通过 contextvars 让业务代码透明取到 trace_id / user_id, 无需层层透传.
    - user_id 由 auth 完成后的入口点 (route / orchestrator / Celery task) 显式 set,
      因为 middleware 早于依赖解析, 此时还拿不到用户.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="")
_USER_ID: ContextVar[str] = ContextVar("user_id", default="")

REQUEST_ID_HEADER = "X-Request-ID"


def current_trace_id() -> str:
    """业务代码可通过此函数取到当前请求的 trace_id."""
    return _TRACE_ID.get()


def current_user_id() -> str:
    """业务代码可通过此函数取到当前请求 / 任务的 user_id.

    未 set 时返回空串 (背景任务 / 系统流量等无用户上下文场景).
    Cost / audit 层据此判断是否记账到具体用户.
    """
    return _USER_ID.get()


def set_user_id(user_id: str):
    """直接 set 当前上下文的 user_id, 返回 Token 用于后续 reset.

    用于不能用 contextmanager 的场景 (例如 async 生成器入口已经在 stream 中).
    """
    return _USER_ID.set(user_id or "")


@contextmanager
def user_id_scope(user_id: str) -> Iterator[None]:
    """显式声明一段 user_id 上下文, 退出时还原.

    用于:
        - Celery task 入口: 任务 kwargs 带 user_id, 进任务时套一层
        - 单测: 不污染全局 ContextVar
    """
    token = _USER_ID.set(user_id or "")
    try:
        yield
    finally:
        _USER_ID.reset(token)


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


class TraceIdLogFilter(logging.Filter):
    """logging Filter: 自动给每条日志记录加上 trace_id / user_id / client_type 字段.

    user_id 在 JSON 日志中出现, 文本日志不影响 (format 字符串没引用 user_id).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _TRACE_ID.get() or "-"
        record.user_id = _USER_ID.get() or "-"
        try:
            from forge.api.middleware.client_type import _CLIENT_TYPE

            record.client_type = _CLIENT_TYPE.get() or "-"
        except Exception:  # noqa: BLE001
            record.client_type = "-"
        return True
