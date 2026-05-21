"""统一 tracing 入口: 屏蔽 exporter 差异.

设计:
    - 一个全局 tracer, 业务层只用 `with span("foo") as s: s.set("k", v)` 这种 API
    - exporter 由配置切换: none / otel / langfuse
    - 关闭状态下走 no-op, 业务代码无需 if 判断

非目标:
    - 不取代 OpenTelemetry 完整功能; 业务有高级需求时直接用 OTel API
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

logger = logging.getLogger(__name__)


class Span(Protocol):
    """span 抽象, 业务只用 set / set_error 两个方法."""

    def set(self, key: str, value) -> None: ...
    def set_error(self, exc: BaseException) -> None: ...


# ----------------------------------------------------------------------
# No-op 实现
# ----------------------------------------------------------------------
class _NoopSpan:
    def set(self, key: str, value) -> None:
        pass

    def set_error(self, exc: BaseException) -> None:
        pass


class _NoopTracer:
    @contextmanager
    def span(self, name: str, **attrs) -> Iterator[Span]:
        yield _NoopSpan()


# ----------------------------------------------------------------------
# 简易日志型 tracer (中间方案, 不需要 OTel 也能看到 trace 结构)
# ----------------------------------------------------------------------
class _LoggingSpan:
    def __init__(self, name: str, attrs: dict) -> None:
        self.name = name
        self.attrs = dict(attrs)
        self.start = time.perf_counter()
        self.error: BaseException | None = None

    def set(self, key: str, value) -> None:
        self.attrs[key] = value

    def set_error(self, exc: BaseException) -> None:
        self.error = exc


class _LoggingTracer:
    @contextmanager
    def span(self, name: str, **attrs) -> Iterator[Span]:
        s = _LoggingSpan(name, attrs)
        try:
            yield s
        except Exception as e:  # noqa: BLE001
            s.set_error(e)
            raise
        finally:
            duration_ms = (time.perf_counter() - s.start) * 1000
            logger.info(
                "span name=%s duration_ms=%.1f attrs=%s error=%s",
                s.name,
                duration_ms,
                s.attrs,
                s.error,
            )


# ----------------------------------------------------------------------
# 全局单例 + 初始化
# ----------------------------------------------------------------------
_tracer: object = _NoopTracer()
_lock = threading.Lock()


def get_tracer() -> object:
    return _tracer


@contextmanager
def span(name: str, **attrs) -> Iterator[Span]:
    """业务侧统一入口. 用法:
    with span("llm.chat", provider="openai") as s:
        s.set("tokens", 100)
        ...
    """
    with _tracer.span(name, **attrs) as s:  # type: ignore[attr-defined]
        yield s


def setup_tracing(
    *,
    enabled: bool,
    exporter: str,
    service_name: str = "forge",
    otel_endpoint: str = "",
    langfuse_public_key: str = "",
    langfuse_secret_key: str = "",
    langfuse_host: str = "",
) -> None:
    """根据配置初始化全局 tracer.

    exporter:
        none      - 无 (no-op, 但仍记日志)
        otel      - OpenTelemetry OTLP
        langfuse  - Langfuse (LLM 专用观测平台)
    """
    global _tracer
    with _lock:
        if not enabled:
            _tracer = _NoopTracer()
            logger.info("tracing 未启用")
            return

        if exporter == "otel":
            try:
                from .otel_exporter import build_otel_tracer

                _tracer = build_otel_tracer(service_name, otel_endpoint)
                logger.info("tracing 启用 OTel 导出, endpoint=%s", otel_endpoint)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("OTel 装配失败, 降级 logging tracer: %s", e)

        if exporter == "langfuse":
            try:
                from .langfuse_exporter import build_langfuse_tracer

                _tracer = build_langfuse_tracer(
                    langfuse_public_key,
                    langfuse_secret_key,
                    langfuse_host,
                )
                logger.info("tracing 启用 Langfuse 导出, host=%s", langfuse_host)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("Langfuse 装配失败, 降级 logging tracer: %s", e)

        # 默认 / fallback: 日志型 tracer (无外部依赖)
        _tracer = _LoggingTracer()
        logger.info("tracing 走 logging 导出 (无外部 sink)")
