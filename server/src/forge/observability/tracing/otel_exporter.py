"""OpenTelemetry OTLP exporter 适配.

依赖 (按需安装, 不在主 requirements):
    pip install opentelemetry-api opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-http

不安装也不影响其他代码 — tracer.setup_tracing() 会捕获 ImportError 并降级.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from forge.observability.tracing.tracer import Span

logger = logging.getLogger(__name__)


def build_otel_tracer(service_name: str, endpoint: str):
    """构造 OTel 的 tracer wrapper, 暴露 .span(name, **attrs) 上下文."""
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource(attributes={"service.name": service_name})
    provider = TracerProvider(resource=resource)
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    otel_tracer = trace.get_tracer(service_name)

    class _OtelSpan(Span):
        def __init__(self, span):
            self._span = span

        def set(self, key: str, value) -> None:
            self._span.set_attribute(key, value)

        def set_error(self, exc: BaseException) -> None:
            self._span.record_exception(exc)
            from opentelemetry.trace import Status, StatusCode

            self._span.set_status(Status(StatusCode.ERROR))

    class _OtelTracer:
        @contextmanager
        def span(self, name: str, **attrs) -> Iterator:
            with otel_tracer.start_as_current_span(name) as s:
                for k, v in attrs.items():
                    try:
                        s.set_attribute(k, v)
                    except Exception:  # noqa: BLE001
                        s.set_attribute(k, str(v))
                yield _OtelSpan(s)

    return _OtelTracer()
