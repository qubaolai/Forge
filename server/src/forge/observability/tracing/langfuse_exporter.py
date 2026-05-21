"""Langfuse tracing 适配.

依赖 (按需安装):
    pip install langfuse

Langfuse 是 LLM-native 观测平台, 比通用 OTel 对 prompt/completion/token
等 LLM 概念支持更好. 设置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 后启用.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def build_langfuse_tracer(public_key: str, secret_key: str, host: str):
    """构造 Langfuse tracer wrapper."""
    from langfuse import Langfuse

    client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host or "https://cloud.langfuse.com",
    )

    class _LFSpan:
        def __init__(self, span):
            self._span = span
            self._attrs: dict = {}

        def set(self, key: str, value) -> None:
            self._attrs[key] = value

        def set_error(self, exc: BaseException) -> None:
            self._attrs["error"] = str(exc)
            self._attrs["status"] = "error"

    class _LFTracer:
        @contextmanager
        def span(self, name: str, **attrs) -> Iterator:
            trace_obj = client.trace(name=name, metadata=dict(attrs))
            s = trace_obj.span(name=name)
            wrapped = _LFSpan(s)
            try:
                yield wrapped
            except Exception as e:  # noqa: BLE001
                wrapped.set_error(e)
                raise
            finally:
                s.end(metadata=wrapped._attrs)

    return _LFTracer()
