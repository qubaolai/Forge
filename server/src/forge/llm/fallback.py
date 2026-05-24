"""LLM Fallback 链: 主 provider 失败时自动切换备用模型.

设计目标:
    - 主调用挂掉 (网络/限流/服务故障) 时自动尝试备用 (client, spec)
    - 兼容 chat / chat_stream / chat_with_tools / chat_with_tools_stream
    - 与 cost_tracker 集成: 每次调用都记账 (无论成功失败)
    - 与 retry 集成: 单 provider 内部先重试, 重试到顶才切下一个

调用方式 (业务层不用直接构造, 走 build_chain_from_settings):
    chain = build_chain_from_settings(settings)
    for chunk in chain.chat_with_tools_stream(messages, tools):
        ...

链元素是 (LLM, LLMCallSpec) 二元组:
    - LLM 是池化的 SDK client (HTTP 连接复用)
    - LLMCallSpec 决定本次调用的 model / temperature / thinking 等
    - 同一 client 可被不同 spec 复用 (主链 & 备用链不同 model 但同 key 时)

流式时, 一旦开始 yield, 不能再换 (语义不一致).
流式 fallback 仅在"首包之前"失败时才切换.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

from config.domains.llm import LLMCallSpec

from forge.guardrails.compliance.audit_logger import (
    LLMCallAuditRecord,
    get_audit_logger,
)
from forge.observability.metrics import llm_metrics

from .caching.prompt_cache import extract_cached_tokens
from .circuit_breaker import get_breaker_registry
from .cost_tracker import estimate_cost, get_cost_tracker
from .providers.base import LLM, ChatChunk, ChatMessage, ChatResult
from .client_pool import get_llm_pool
from .retry import call_with_retry, is_rate_limit, parse_retry_after

logger = logging.getLogger(__name__)

ChainEntry = tuple[LLM, LLMCallSpec]


def _call_kwargs(spec: LLMCallSpec) -> dict[str, Any]:
    """将 spec 展开成 chat 方法的关键字参数, 过滤 None."""
    kw: dict[str, Any] = {
        "model": spec.model,
        "temperature": spec.temperature,
        "max_tokens": spec.max_tokens,
        "top_p": spec.top_p,
        "thinking": spec.thinking,
        "reasoning_effort": spec.reasoning_effort,
        "thinking_budget": spec.thinking_budget,
        "top_k": spec.top_k,
    }
    return {k: v for k, v in kw.items() if v is not None or k == "model"}


def _extra_options(spec: LLMCallSpec, runtime_options: dict[str, Any] | None) -> dict[str, Any] | None:
    """合并 DB 模型参数和运行时参数，运行时参数优先。"""
    merged = dict(spec.extra or {})
    if runtime_options:
        merged.update(runtime_options)
    return merged or None


class LLMFallbackChain:
    """主 + 备用 LLM (client, spec) 列表, 按顺序调用直到成功."""

    def __init__(
        self,
        primary: ChainEntry,
        fallbacks: list[ChainEntry] | None = None,
        *,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self._chain: list[ChainEntry] = [primary, *(fallbacks or [])]
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds
        self._cost = get_cost_tracker()
        self._breakers = get_breaker_registry()
        self._audit = get_audit_logger()

    def _emit_audit(
        self,
        client: LLM,
        spec: LLMCallSpec,
        idx: int,
        *,
        usage: dict | None = None,
        started_at: float,
        finish_reason: str | None = None,
        error: str | None = None,
        breaker_skipped: bool = False,
    ) -> None:
        """统一发审计日志 + Prometheus 指标, 失败 / 成功 / 跳过都走这里."""
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        prompt = self._extract_usage(usage, ("prompt_tokens", "input_tokens"))
        completion = self._extract_usage(usage, ("completion_tokens", "output_tokens"))
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

        # Prometheus 指标 (软依赖, no-op 安全)
        status = "circuit_open" if breaker_skipped else ("error" if error else "success")
        llm_metrics.record_request(provider, spec.model, status)
        if not breaker_skipped:
            llm_metrics.record_latency(provider, spec.model, latency_ms / 1000.0)
        if prompt or completion:
            llm_metrics.record_tokens(provider, spec.model, prompt, completion)
        if cost > 0:
            from forge.core.request_context import current_user_id

            llm_metrics.record_cost(provider, spec.model, current_user_id(), cost)
        if cached_tokens > 0:
            llm_metrics.record_cache_tokens(provider, spec.model, cached_tokens)

    @staticmethod
    def _extract_usage(usage: dict | None, keys: tuple[str, ...]) -> int:
        if not usage:
            return 0
        for k in keys:
            v = usage.get(k)
            if isinstance(v, int | float):
                return int(v)
        return 0

    @property
    def primary(self) -> LLM:
        return self._chain[0][0]

    @property
    def primary_spec(self) -> LLMCallSpec:
        return self._chain[0][1]

    @property
    def supports_tool_calling(self) -> bool:
        return any(c.supports_tool_calling for c, _ in self._chain)

    def _check_budget_for(self, spec: LLMCallSpec) -> None:
        self._cost.check_budget(quota_controlled=spec.quota_controlled)

    def _record_cost_for(
        self,
        client: LLM,
        spec: LLMCallSpec,
        usage: dict | None,
        *,
        error: bool = False,
    ) -> None:
        self._cost.record(
            client.provider_name,
            spec.model,
            usage,
            error=error,
            quota_controlled=spec.quota_controlled,
        )

    @staticmethod
    def _entry_group(spec: LLMCallSpec) -> tuple[str, str]:
        """同一 provider/model 下的不同 key 属于同一个 key 级切换组。"""
        return (spec.provider_name or spec.impl, spec.model)

    def _mark_key_cooldown_if_rate_limited(
        self,
        spec: LLMCallSpec,
        exc: BaseException,
        *,
        fallback_seconds: float = 30.0,
    ) -> bool:
        """检测 429 并将当前 key 放入冷却。"""
        if not spec.api_key or not is_rate_limit(exc):
            return False
        seconds = parse_retry_after(exc) or fallback_seconds
        get_llm_pool().mark_cooldown(spec.impl, spec.api_key, seconds)
        logger.warning(
            "Key 级限流处理完成: provider=%s impl=%s model=%s key=%s cooldown=%.0fs",
            spec.provider_name or spec.impl,
            spec.impl,
            spec.model,
            spec.api_key[:6] + "***",
            seconds,
        )
        return True

    def _on_retry_for_key(self, spec: LLMCallSpec):
        """429 时停止当前 key 的重试，让链路切换到下一个可用 key。"""

        def _callback(attempt: int, exc: BaseException, delay: float) -> bool | None:
            if self._mark_key_cooldown_if_rate_limited(spec, exc, fallback_seconds=delay):
                return False
            return None

        return _callback

    # ------------------------------------------------------------------
    # chat: 非流式
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> ChatResult:
        last_exc: BaseException | None = None
        blocked_key_group: tuple[str, str] | None = None
        for idx, (client, spec) in enumerate(self._chain):
            group = self._entry_group(spec)
            if blocked_key_group == group:
                logger.warning(
                    "跳过同模型 Key 级切换: provider=%s model=%s reason=非限流错误",
                    group[0],
                    group[1],
                )
                continue
            breaker = self._breakers.get(spec.impl, spec.api_key, spec.model)
            if breaker.is_open():
                logger.warning(
                    "熔断器开启, 跳过 位置=%d provider=%s model=%s",
                    idx,
                    client.provider_name,
                    spec.model,
                )
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    started_at=time.perf_counter(),
                    breaker_skipped=True,
                    error="circuit_breaker_open",
                )
                continue
            self._check_budget_for(spec)
            started_at = time.perf_counter()
            try:
                kw = _call_kwargs(spec)
                if temperature is not None:
                    kw["temperature"] = temperature
                if max_tokens is not None:
                    kw["max_tokens"] = max_tokens

                # 默认参数绑定: 防止闭包捕获循环变量
                def _call_chat(
                    llm: LLM = client,
                    call_kwargs: dict[str, Any] = kw,
                ) -> ChatResult:
                    return llm.chat(
                        messages,
                        extra_options=_extra_options(spec, extra_options),
                        **call_kwargs,
                    )

                result = call_with_retry(
                    _call_chat,
                    max_retries=self._max_retries,
                    backoff_seconds=self._retry_backoff,
                    on_retry=self._on_retry_for_key(spec),
                )
                self._record_cost_for(client, spec, result.usage)
                breaker.record_success()
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    usage=result.usage,
                    started_at=started_at,
                    finish_reason="stop",
                )
                if idx > 0:
                    logger.info(
                        "LLM fallback 成功: 位置=%d provider=%s model=%s",
                        idx,
                        client.provider_name,
                        spec.model,
                    )
                return result
            except Exception as e:  # noqa: BLE001
                last_exc = e
                was_rate_limited = self._mark_key_cooldown_if_rate_limited(spec, e)
                if not was_rate_limited:
                    blocked_key_group = group
                self._record_cost_for(client, spec, None, error=True)
                breaker.record_failure()
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    started_at=started_at,
                    error=type(e).__name__,
                )
                logger.warning(
                    "LLM 调用失败 (位置=%d provider=%s model=%s): %s",
                    idx,
                    client.provider_name,
                    spec.model,
                    e,
                )
                if was_rate_limited:
                    logger.warning(
                        "Key 级切换：provider/model 不变，尝试下一个可用 key provider=%s model=%s",
                        spec.provider_name or spec.impl,
                        spec.model,
                    )
        assert last_exc is not None
        logger.error("LLM fallback 链全部失败 (%d 个)", len(self._chain))
        raise last_exc

    # ------------------------------------------------------------------
    # chat_stream: 流式
    # ------------------------------------------------------------------
    def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> Iterator[ChatChunk]:
        """流式: 首包前可切换, 一旦开始 yield 就锁定."""
        last_exc: BaseException | None = None
        blocked_key_group: tuple[str, str] | None = None
        for idx, (client, spec) in enumerate(self._chain):
            group = self._entry_group(spec)
            if blocked_key_group == group:
                logger.warning(
                    "跳过同模型 Key 级切换: provider=%s model=%s reason=非限流错误",
                    group[0],
                    group[1],
                )
                continue
            breaker = self._breakers.get(spec.impl, spec.api_key, spec.model)
            if breaker.is_open():
                logger.warning(
                    "熔断器开启, 跳过 stream 位置=%d provider=%s model=%s",
                    idx,
                    client.provider_name,
                    spec.model,
                )
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    started_at=time.perf_counter(),
                    breaker_skipped=True,
                    error="circuit_breaker_open",
                )
                continue
            self._check_budget_for(spec)
            started_at = time.perf_counter()
            try:
                kw = _call_kwargs(spec)
                if temperature is not None:
                    kw["temperature"] = temperature
                if max_tokens is not None:
                    kw["max_tokens"] = max_tokens

                stream = client.chat_stream(
                    messages,
                    extra_options=_extra_options(spec, extra_options),
                    **kw,
                )
                first = next(stream)
                if idx > 0:
                    logger.info(
                        "LLM stream fallback 成功: 位置=%d provider=%s",
                        idx,
                        client.provider_name,
                    )
                yield first
                final_usage: dict | None = None
                for chunk in stream:
                    if chunk.usage:
                        final_usage = chunk.usage
                    yield chunk
                self._record_cost_for(client, spec, final_usage)
                breaker.record_success()
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    usage=final_usage,
                    started_at=started_at,
                    finish_reason="stop",
                )
                return
            except StopIteration:
                last_exc = RuntimeError(f"{client.provider_name} 流式输出为空")
                blocked_key_group = group
                self._record_cost_for(client, spec, None, error=True)
                breaker.record_failure()
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    started_at=started_at,
                    error="empty_stream",
                )
                logger.warning("LLM stream 位置=%d 输出为空, 切换备用", idx)
            except Exception as e:  # noqa: BLE001
                last_exc = e
                was_rate_limited = self._mark_key_cooldown_if_rate_limited(spec, e)
                if not was_rate_limited:
                    blocked_key_group = group
                self._record_cost_for(client, spec, None, error=True)
                breaker.record_failure()
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    started_at=started_at,
                    error=type(e).__name__,
                )
                logger.warning(
                    "LLM stream 启动失败 (位置=%d provider=%s): %s",
                    idx,
                    client.provider_name,
                    e,
                )
                if was_rate_limited:
                    logger.warning(
                        "Key 级切换：provider/model 不变，尝试下一个可用 key provider=%s model=%s",
                        spec.provider_name or spec.impl,
                        spec.model,
                    )
        assert last_exc is not None
        logger.error("LLM stream fallback 链全部失败")
        raise last_exc

    # ------------------------------------------------------------------
    # chat_with_tools: 非流式 tool calling
    # ------------------------------------------------------------------
    def chat_with_tools(
        self,
        messages: list,
        tools: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str = "auto",
        extra_options: dict[str, Any] | None = None,
    ) -> dict:
        last_exc: BaseException | None = None
        blocked_key_group: tuple[str, str] | None = None
        for idx, (client, spec) in enumerate(self._chain):
            group = self._entry_group(spec)
            if blocked_key_group == group:
                logger.warning(
                    "跳过同模型 Key 级切换: provider=%s model=%s reason=非限流错误",
                    group[0],
                    group[1],
                )
                continue
            if not client.supports_tool_calling:
                logger.debug(
                    "LLM 位置=%d provider=%s 不支持 tool calling, 跳过",
                    idx,
                    client.provider_name,
                )
                continue
            breaker = self._breakers.get(spec.impl, spec.api_key, spec.model)
            if breaker.is_open():
                logger.warning(
                    "熔断器开启, 跳过 tool 位置=%d provider=%s model=%s",
                    idx,
                    client.provider_name,
                    spec.model,
                )
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    started_at=time.perf_counter(),
                    breaker_skipped=True,
                    error="circuit_breaker_open",
                )
                continue
            self._check_budget_for(spec)
            started_at = time.perf_counter()
            try:
                kw = _call_kwargs(spec)
                if temperature is not None:
                    kw["temperature"] = temperature
                if max_tokens is not None:
                    kw["max_tokens"] = max_tokens

                def _call_chat_with_tools(
                    llm: LLM = client,
                    call_kwargs: dict[str, Any] = kw,
                ) -> dict:
                    return llm.chat_with_tools(
                        messages,
                        tools,
                        tool_choice=tool_choice,
                        extra_options=_extra_options(spec, extra_options),
                        **call_kwargs,
                    )

                resp = call_with_retry(
                    _call_chat_with_tools,
                    max_retries=self._max_retries,
                    backoff_seconds=self._retry_backoff,
                    on_retry=self._on_retry_for_key(spec),
                )
                self._record_cost_for(client, spec, resp.get("usage"))
                breaker.record_success()
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    usage=resp.get("usage"),
                    started_at=started_at,
                    finish_reason=resp.get("finish_reason")
                    or ("tool_calls" if resp.get("tool_calls") else "stop"),
                )
                if idx > 0:
                    logger.info(
                        "LLM tool fallback 成功: 位置=%d provider=%s model=%s",
                        idx,
                        client.provider_name,
                        spec.model,
                    )
                return resp
            except Exception as e:  # noqa: BLE001
                last_exc = e
                was_rate_limited = self._mark_key_cooldown_if_rate_limited(spec, e)
                if not was_rate_limited:
                    blocked_key_group = group
                self._record_cost_for(client, spec, None, error=True)
                breaker.record_failure()
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    started_at=started_at,
                    error=type(e).__name__,
                )
                logger.warning(
                    "LLM tool 调用失败 (位置=%d provider=%s model=%s): %s",
                    idx,
                    client.provider_name,
                    spec.model,
                    e,
                )
                if was_rate_limited:
                    logger.warning(
                        "Key 级切换：provider/model 不变，尝试下一个可用 key provider=%s model=%s",
                        spec.provider_name or spec.impl,
                        spec.model,
                    )
        if last_exc is None:
            raise RuntimeError("Fallback 链中没有任何 provider 支持 tool calling")
        logger.error("LLM tool fallback 链全部失败")
        raise last_exc

    # ------------------------------------------------------------------
    # chat_with_tools_stream: 流式 tool calling
    # ------------------------------------------------------------------
    def chat_with_tools_stream(
        self,
        messages: list,
        tools: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str = "auto",
        extra_options: dict[str, Any] | None = None,
    ) -> Iterator[dict]:
        last_exc: BaseException | None = None
        blocked_key_group: tuple[str, str] | None = None
        for idx, (client, spec) in enumerate(self._chain):
            group = self._entry_group(spec)
            if blocked_key_group == group:
                logger.warning(
                    "跳过同模型 Key 级切换: provider=%s model=%s reason=非限流错误",
                    group[0],
                    group[1],
                )
                continue
            if not client.supports_tool_calling:
                logger.debug(
                    "LLM 位置=%d provider=%s 不支持 tool calling, 跳过",
                    idx,
                    client.provider_name,
                )
                continue
            breaker = self._breakers.get(spec.impl, spec.api_key, spec.model)
            if breaker.is_open():
                logger.warning(
                    "熔断器开启, 跳过 tool stream 位置=%d provider=%s model=%s",
                    idx,
                    client.provider_name,
                    spec.model,
                )
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    started_at=time.perf_counter(),
                    breaker_skipped=True,
                    error="circuit_breaker_open",
                )
                continue
            self._check_budget_for(spec)
            started_at = time.perf_counter()
            try:
                kw = _call_kwargs(spec)
                if temperature is not None:
                    kw["temperature"] = temperature
                if max_tokens is not None:
                    kw["max_tokens"] = max_tokens

                stream = client.chat_with_tools_stream(
                    messages,
                    tools,
                    tool_choice=tool_choice,
                    extra_options=_extra_options(spec, extra_options),
                    **kw,
                )
                first = next(stream)
                if idx > 0:
                    logger.info(
                        "LLM tool stream fallback 成功: 位置=%d provider=%s",
                        idx,
                        client.provider_name,
                    )
                yield first
                final_usage: dict | None = None
                final_reason: str | None = None
                for chunk in stream:
                    if chunk.get("usage"):
                        final_usage = chunk["usage"]
                    if chunk.get("finish_reason"):
                        final_reason = chunk["finish_reason"]
                    yield chunk
                self._record_cost_for(client, spec, final_usage)
                breaker.record_success()
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    usage=final_usage,
                    started_at=started_at,
                    finish_reason=final_reason or "stop",
                )
                return
            except StopIteration:
                last_exc = RuntimeError(f"{client.provider_name} tool stream 输出为空")
                blocked_key_group = group
                self._record_cost_for(client, spec, None, error=True)
                breaker.record_failure()
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    started_at=started_at,
                    error="empty_stream",
                )
                logger.warning("LLM tool stream 位置=%d 输出为空, 切换备用", idx)
            except Exception as e:  # noqa: BLE001
                last_exc = e
                was_rate_limited = self._mark_key_cooldown_if_rate_limited(spec, e)
                if not was_rate_limited:
                    blocked_key_group = group
                self._record_cost_for(client, spec, None, error=True)
                breaker.record_failure()
                self._emit_audit(
                    client,
                    spec,
                    idx,
                    started_at=started_at,
                    error=type(e).__name__,
                )
                logger.warning(
                    "LLM tool stream 启动失败 (位置=%d provider=%s): %s",
                    idx,
                    client.provider_name,
                    e,
                )
                if was_rate_limited:
                    logger.warning(
                        "Key 级切换：provider/model 不变，尝试下一个可用 key provider=%s model=%s",
                        spec.provider_name or spec.impl,
                        spec.model,
                    )
        if last_exc is None:
            raise RuntimeError("Fallback 链中没有任何 provider 支持 tool calling")
        logger.error("LLM tool stream fallback 链全部失败")
        raise last_exc
