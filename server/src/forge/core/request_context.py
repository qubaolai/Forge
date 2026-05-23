"""请求上下文 — 跨层共享的 ContextVar 声明和访问器。

设计原则:
    - 所有 ContextVar 在此声明，基础设施层和 API 层都从本模块导入
    - 避免基础设施层反向依赖 API 中间件
    - 访问器（current_* / set_*）是公共 API，私有 ContextVar 不直接暴露
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal, cast

_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="")
_USER_ID: ContextVar[str] = ContextVar("user_id", default="")

ClientType = Literal["cli", "web"]
_CLIENT_TYPES: frozenset[str] = frozenset({"cli", "web"})
_CLIENT_TYPE: ContextVar[ClientType] = ContextVar("client_type", default="cli")

CLIENT_TYPE_HEADER = "X-Client-Type"
REQUEST_ID_HEADER = "X-Request-ID"


# ---- trace_id ----
def current_trace_id() -> str:
    return _TRACE_ID.get()


def set_trace_id(trace_id: str) -> None:
    _TRACE_ID.set(trace_id)


# ---- user_id ----
def current_user_id() -> str:
    return _USER_ID.get()


def set_user_id(user_id: str):
    return _USER_ID.set(user_id or "")


@contextmanager
def user_id_scope(user_id: str) -> Iterator[None]:
    token = _USER_ID.set(user_id or "")
    try:
        yield
    finally:
        _USER_ID.reset(token)


# ---- client_type ----
def current_client_type() -> ClientType:
    return _CLIENT_TYPE.get()


def set_client_type(client_type: str):
    return _CLIENT_TYPE.set(_normalize_client_type(client_type))


def infer_client_type(header_value: str | None, user_agent: str | None = None) -> ClientType:
    if header_value:
        return _normalize_client_type(header_value)
    ua = (user_agent or "").lower()
    if any(mark in ua for mark in ("mozilla/", "chrome/", "safari/", "firefox/")):
        return "web"
    return "cli"


def _normalize_client_type(value: str) -> ClientType:
    raw = (value or "").strip().lower()
    if raw in _CLIENT_TYPES:
        return cast(ClientType, raw)
    return "cli"


# ---- 日志过滤器 ----
class RequestContextLogFilter(logging.Filter):
    """logging Filter: 自动给每条日志记录加上 trace_id / user_id / client_type."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _TRACE_ID.get() or "-"
        record.user_id = _USER_ID.get() or "-"
        record.client_type = _CLIENT_TYPE.get() or "-"
        return True
