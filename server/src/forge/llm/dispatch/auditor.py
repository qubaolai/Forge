"""DispatchAuditor: 将每次 LLM 调用 (成功 / 失败 / 熔断跳过) 上报到审计 + Prometheus.

原 LLMDispatcher._emit_audit 内嵌实现, 行数过多且与链遍历职责无关.
拆出后:
    - LLMDispatcher 只需持有一个 DispatchAuditor, 在三个关键位置回调 emit()
    - 测试可以注入 stub auditor 验证调用时机
    - 未来新增审计后端 (例如 OpenTelemetry Span 注入) 不需要再改 dispatcher
"""

from __future__ import annotations

import logging
import time

from forge.config.domains.llm import LLMCallSpec
from forge.guardrails.compliance.audit_logger import (
    AuditLogger,
    LLMCallAuditRecord,
    get_audit_logger,
)
from forge.observability.metrics import llm_metrics

from ..caching.prompt_cache import extract_cached_tokens
from ..cost_tracker import estimate_cost
from ..providers.base import LLM

logger = logging.getLogger(__name__)


def _extract_usage(usage: dict | None, keys: tuple[str, ...]) -> int:
    if not usage:
        return 0
    for k in keys:
        v = usage.get(k)
        if isinstance(v, int | float):
            return int(v)
    return 0


class DispatchAuditor:
    """LLM 调用审计 + 指标上报. 单例可被注入到 LLMDispatcher."""

    def __init__(self, audit_logger: AuditLogger | None = None) -> None:
        self._audit = audit_logger or get_audit_logger()

    def emit(
        self,
        client: LLM,
        spec: LLMCallSpec,
        idx: int,
        *,
        started_at: float,
        usage: dict | None = None,
        finish_reason: str | None = None,
        error: str | None = None,
        breaker_skipped: bool = False,
    ) -> None:
        """统一发审计日志 + Prometheus 指标."""
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        prompt = _extract_usage(usage, ("prompt_tokens", "input_tokens"))
        completion = _extract_usage(usage, ("completion_tokens", "output_tokens"))
        cost = estimate_cost(spec.model, prompt, completion)
        provider = client.provider_name
        cached_tokens = extract_cached_tokens(usage, provider)

        self._audit.log(
            LLMCallAuditRecord(
                provider=provider,
                model=spec.model,
                prompt_tokens=prompt,
                completion_tokens=completion,
                estimated_cost_usd=cost,
                latency_ms=latency_ms,
                api_key_fingerprint=client.api_key_fingerprint,
                fallback_position=idx,
                finish_reason=finish_reason,
                circuit_breaker_skipped=breaker_skipped,
                error=error,
                cached_tokens=cached_tokens,
                cache_hit=cached_tokens > 0,
                cache_type="prompt_native" if cached_tokens > 0 else None,
            )
        )

        status = (
            "circuit_open" if breaker_skipped
            else ("error" if error else "success")
        )
        llm_metrics.record_request(provider, spec.model, status)
        if not breaker_skipped:
            llm_metrics.record_latency(provider, spec.model, latency_ms / 1000.0)
        if prompt or completion:
            llm_metrics.record_tokens(provider, spec.model, prompt, completion)
        if cost > 0:
            # 延迟导入避免循环依赖
            from forge.core.request_context import current_user_id

            llm_metrics.record_cost(provider, spec.model, current_user_id(), cost)
        if cached_tokens > 0:
            llm_metrics.record_cache_tokens(provider, spec.model, cached_tokens)


_default: DispatchAuditor | None = None


def get_dispatch_auditor() -> DispatchAuditor:
    """全局默认 DispatchAuditor 单例."""
    global _default
    if _default is None:
        _default = DispatchAuditor()
    return _default


__all__ = ["DispatchAuditor", "get_dispatch_auditor"]
