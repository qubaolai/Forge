"""LLMDispatcher: LLM 调用链遍历调度核心.

职责 (Phase 1):
    - 链遍历 (按顺序尝试 (client, spec) 候选)
    - 熔断检查 (跳过 OPEN 的 entry)
    - 重试 (单 entry 内部按 RetryPolicy)
    - Key 冷却 (429 后切下一个 Key, 但不跳出 provider/model)
    - Fallback 跳转 (非 429 错误 → 切下一组 (provider, model))
    - 预算检查 / 成本记账 / 审计日志 (Phase 2 拆到 pipeline)

设计要点:
    - 4 个 chat 方法 (chat / chat_stream / chat_with_tools / chat_with_tools_stream)
      共享 3 个辅助方法: _check_entry_can_proceed / _record_success / _record_failure
    - 流式 fallback 仅在"首包之前"失败时切换, 一旦开始 yield 就锁定
    - LLMDispatcher 等价于 (但语义更清晰的) LLMFallbackChain
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from forge.config.domains.llm import LLMCallSpec, TimeoutConfig

from ..client_pool import get_llm_pool
from ..cost_tracker import get_cost_tracker
from ..providers.base import LLM, ChatChunk, ChatMessage, ChatResult
from ..resilience.bulkhead import (
    BulkheadRejectError,
    ProviderBulkhead,
    get_bulkhead,
)
from ..resilience.circuit_breaker import (
    CircuitBreakerStrategy,
    get_breaker_strategy,
)
from ..resilience.retry import (
    RetryPolicy,
    call_with_retry,
    is_rate_limit,
    parse_retry_after,
)
from ..streaming import FirstTokenTimeoutError, stream_with_first_token_timeout, with_total_timeout
from .auditor import DispatchAuditor, get_dispatch_auditor

logger = logging.getLogger(__name__)

ChainEntry = tuple[LLM, LLMCallSpec]


class _EmptyStream(Exception):
    """流首包前即结束: 视为 provider 输出为空, 触发 fallback."""


async def _stream_first(stream: Any) -> tuple[Any, Any]:
    """从异步或同步迭代器取首个 chunk, 同时返回已激活的迭代器.

    - 空流抛 _EmptyStream
    - 返回 (first_chunk, iterator) 由 _stream_iter 继续消费剩余 chunk
    """
    if hasattr(stream, "__aiter__"):
        aiter_ = stream.__aiter__()
        try:
            first = await aiter_.__anext__()
        except StopAsyncIteration as e:
            raise _EmptyStream() from e
        return first, ("async", aiter_)
    if hasattr(stream, "__iter__"):
        it = iter(stream)
        try:
            first = next(it)
        except StopIteration as e:
            raise _EmptyStream() from e
        return first, ("sync", it)
    raise TypeError(f"流式 stream 既非 async 也非 sync iterable: {type(stream).__name__}")


async def _stream_iter(_unused: Any, iter_state: tuple[str, Any]) -> AsyncIterator[Any]:
    """统一以 async iterator 形式继续消费 _stream_first 返回的迭代器."""
    kind, it = iter_state
    if kind == "async":
        while True:
            try:
                chunk = await it.__anext__()
            except StopAsyncIteration:
                return
            yield chunk
    else:
        for chunk in it:
            yield chunk


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


def _extra_options(
    spec: LLMCallSpec, runtime_options: dict[str, Any] | None
) -> dict[str, Any] | None:
    """合并 DB 模型参数和运行时参数, 运行时参数优先."""
    merged = dict(spec.extra or {})
    if runtime_options:
        merged.update(runtime_options)
    return merged or None


def _entry_group(spec: LLMCallSpec) -> tuple[str, str]:
    """同一 provider/model 下的不同 key 属于同一 key 级切换组."""
    return (spec.provider_name or spec.impl, spec.model)


class LLMDispatcher:
    """主 + 备用 LLM (client, spec) 列表, 按顺序调用直到成功.

    Phase 1 暂保留预算/审计/成本记账内嵌, Phase 2 将其拆到 pipeline.
    """

    def __init__(
        self,
        primary: ChainEntry,
        fallbacks: list[ChainEntry] | None = None,
        *,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
        breaker_strategy: CircuitBreakerStrategy | None = None,
        retry_policy: RetryPolicy | None = None,
        bulkhead: ProviderBulkhead | None = None,
        auditor: DispatchAuditor | None = None,
        timeout_config: TimeoutConfig | None = None,
    ) -> None:
        self._chain: list[ChainEntry] = [primary, *(fallbacks or [])]
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff_seconds
        self._retry_policy = retry_policy
        self._cost = get_cost_tracker()
        self._breakers = breaker_strategy or get_breaker_strategy()
        self._bulkhead = bulkhead or get_bulkhead()
        self._auditor = auditor or get_dispatch_auditor()
        self._timeout = timeout_config or TimeoutConfig()
        self._last_fallback_position: int = 0

    @property
    def primary(self) -> LLM:
        return self._chain[0][0]

    @property
    def primary_spec(self) -> LLMCallSpec:
        return self._chain[0][1]

    @property
    def supports_tool_calling(self) -> bool:
        return any(c.supports_tool_calling for c, _ in self._chain)

    @property
    def last_fallback_position(self) -> int:
        """最后一次成功调用所用的 fallback 位次 (0=主模型, ≥1=备用)."""
        return self._last_fallback_position

    # ------------------------------------------------------------------
    # 共享辅助方法 (消除 4x 重复)
    # ------------------------------------------------------------------
    def _check_entry_can_proceed(
        self,
        client: LLM,
        spec: LLMCallSpec,
        idx: int,
        blocked_key_group: tuple[str, str] | None,
        *,
        require_tool_support: bool = False,
        phase: str = "chat",
    ) -> bool:
        """判断当前 entry 是否可以尝试调用. 返回 False 表示跳过."""
        group = _entry_group(spec)
        if blocked_key_group == group:
            logger.warning(
                "跳过同模型 Key 级切换: provider=%s model=%s reason=非限流错误",
                group[0],
                group[1],
            )
            return False
        if require_tool_support and not client.supports_tool_calling:
            logger.debug(
                "LLM 位置=%d provider=%s 不支持 tool calling, 跳过",
                idx,
                client.provider_name,
            )
            return False
        breaker_key = (spec.impl, spec.api_key)
        if self._breakers.is_open(breaker_key):
            logger.warning(
                "熔断器开启, 跳过 %s 位置=%d provider=%s model=%s",
                phase,
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
            return False
        return True

    def _record_success(
        self,
        client: LLM,
        spec: LLMCallSpec,
        idx: int,
        usage: dict | None,
        started_at: float,
        *,
        finish_reason: str | None = "stop",
        phase: str = "chat",
    ) -> None:
        """记录成功: 成本 + 熔断 + 审计."""
        self._last_fallback_position = idx
        self._record_cost_for(client, spec, usage)
        self._breakers.record_success((spec.impl, spec.api_key))
        self._emit_audit(
            client,
            spec,
            idx,
            usage=usage,
            started_at=started_at,
            finish_reason=finish_reason,
        )
        if idx > 0:
            logger.info(
                "LLM %s fallback 成功: 降级到 [%s:%s] (位置=%d)",
                phase,
                client.provider_name,
                spec.model,
                idx,
            )

    def _record_failure(
        self,
        client: LLM,
        spec: LLMCallSpec,
        idx: int,
        exc: BaseException,
        started_at: float,
        *,
        phase: str = "chat",
    ) -> bool:
        """记录失败: 成本错误 + 熔断 + 审计 + Key 冷却. 返回 True 表示 429 (key 级切换), False 表示需要跳到下个 provider."""
        was_rate_limited = self._mark_key_cooldown_if_rate_limited(spec, exc)
        self._record_cost_for(client, spec, None, error=True)
        self._breakers.record_failure((spec.impl, spec.api_key))
        self._emit_audit(
            client,
            spec,
            idx,
            started_at=started_at,
            error=type(exc).__name__,
        )
        logger.warning(
            "LLM %s 调用失败 [%s:%s] (位置=%d): %s",
            phase,
            client.provider_name,
            spec.model,
            idx,
            exc,
        )
        if was_rate_limited:
            logger.warning(
                "Key 级切换: provider/model 不变, 尝试下一个可用 key provider=%s model=%s",
                spec.provider_name or spec.impl,
                spec.model,
            )
        return was_rate_limited

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
        """委派给 DispatchAuditor (从 Phase 1 内嵌实现拆出)."""
        self._auditor.emit(
            client,
            spec,
            idx,
            started_at=started_at,
            usage=usage,
            finish_reason=finish_reason,
            error=error,
            breaker_skipped=breaker_skipped,
        )

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

    def _mark_key_cooldown_if_rate_limited(
        self,
        spec: LLMCallSpec,
        exc: BaseException,
        *,
        fallback_seconds: float = 30.0,
    ) -> bool:
        """检测 429 并将当前 key 放入冷却."""
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
        """429 时停止当前 key 的重试, 让链路切换到下一个可用 key."""

        def _callback(attempt: int, exc: BaseException, delay: float) -> bool | None:
            if self._mark_key_cooldown_if_rate_limited(spec, exc, fallback_seconds=delay):
                return False
            return None

        return _callback

    # ------------------------------------------------------------------
    # chat: 非流式
    # ------------------------------------------------------------------
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> ChatResult:
        async def call(client: LLM, spec: LLMCallSpec) -> ChatResult:
            kw = _call_kwargs(spec)
            if temperature is not None:
                kw["temperature"] = temperature
            if max_tokens is not None:
                kw["max_tokens"] = max_tokens
            return await client.chat(
                messages,
                extra_options=_extra_options(spec, extra_options),
                **kw,
            )

        return await self._dispatch_non_stream(
            call,
            extract_usage=lambda r: r.usage,
            phase="chat",
        )

    # ------------------------------------------------------------------
    # chat_with_tools: 非流式 tool calling
    # ------------------------------------------------------------------
    async def chat_with_tools(
        self,
        messages: list,
        tools: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        extra_options: dict[str, Any] | None = None,
    ) -> dict:
        async def call(client: LLM, spec: LLMCallSpec) -> dict:
            kw = _call_kwargs(spec)
            if temperature is not None:
                kw["temperature"] = temperature
            if max_tokens is not None:
                kw["max_tokens"] = max_tokens
            return await client.chat_with_tools(
                messages,
                tools,
                tool_choice=tool_choice,
                extra_options=_extra_options(spec, extra_options),
                **kw,
            )

        return await self._dispatch_non_stream(
            call,
            extract_usage=lambda r: r.get("usage"),
            extract_finish_reason=lambda r: r.get("finish_reason")
            or ("tool_calls" if r.get("tool_calls") else "stop"),
            require_tool_support=True,
            phase="tool",
            empty_chain_error="Fallback 链中没有任何 provider 支持 tool calling",
        )

    # ------------------------------------------------------------------
    # 共享非流式调度
    # ------------------------------------------------------------------
    async def _dispatch_non_stream(
        self,
        call_fn: Callable[[LLM, LLMCallSpec], Awaitable[Any]],
        *,
        extract_usage: Callable[[Any], dict | None],
        extract_finish_reason: Callable[[Any], str | None] = lambda _: "stop",
        require_tool_support: bool = False,
        phase: str = "chat",
        empty_chain_error: str | None = None,
    ) -> Any:
        last_exc: BaseException | None = None
        blocked_key_group: tuple[str, str] | None = None
        for idx, (client, spec) in enumerate(self._chain):
            if not self._check_entry_can_proceed(
                client,
                spec,
                idx,
                blocked_key_group,
                require_tool_support=require_tool_support,
                phase=phase,
            ):
                continue
            self._check_budget_for(spec)
            started_at = time.perf_counter()
            try:
                async def _wrapped(client=client, spec=spec):
                    return await call_fn(client, spec)

                async with self._bulkhead.guard(client.provider_name):
                    result = await with_total_timeout(
                        call_with_retry(
                            _wrapped,
                            max_retries=self._max_retries,
                            backoff_seconds=self._retry_backoff,
                            on_retry=self._on_retry_for_key(spec),
                            policy=self._retry_policy,
                        ),
                        self._timeout.total_timeout_s,
                    )
                self._record_success(
                    client,
                    spec,
                    idx,
                    extract_usage(result),
                    started_at,
                    finish_reason=extract_finish_reason(result),
                    phase=phase,
                )
                return result
            except BulkheadRejectError as e:
                # 舱壁拒绝: 当前 provider 已饱和, 切下一个 (按 provider 跳过, 不影响其他 key)
                last_exc = e
                self._emit_audit(
                    client, spec, idx, started_at=started_at, error="bulkhead_reject"
                )
                logger.warning(
                    "舱壁拒绝, 切下一个 provider: %s %s/%s",
                    phase, client.provider_name, spec.model,
                )
                blocked_key_group = _entry_group(spec)
            except Exception as e:  # noqa: BLE001
                last_exc = e
                was_rate_limited = self._record_failure(
                    client, spec, idx, e, started_at, phase=phase
                )
                if not was_rate_limited:
                    blocked_key_group = _entry_group(spec)
        if last_exc is None:
            raise RuntimeError(empty_chain_error or f"Fallback 链中没有可用的 {phase} provider")
        logger.error("LLM %s fallback 链全部失败 (%d 个)", phase, len(self._chain))
        raise last_exc

    # ------------------------------------------------------------------
    # 流式调度共享辅助 (消除 chat_stream / chat_with_tools_stream 重复)
    # ------------------------------------------------------------------
    async def _dispatch_stream(
        self,
        open_stream_fn: Callable[[LLM, LLMCallSpec], Any],
        *,
        extract_usage: Callable[[Any], dict | None],
        extract_finish_reason: Callable[[Any], str | None] = lambda _: None,
        require_tool_support: bool = False,
        phase: str = "stream",
        empty_chain_error: str | None = None,
    ) -> AsyncIterator[Any]:
        """统一流式调度: 链遍历 + bulkhead + 首包前 fallback + 一旦 yield 即锁定.

        open_stream_fn(client, spec) → 返回 stream iterator (异步或同步), 后续统一按
        AsyncIterator / Iterator 双形态消费.
        """
        last_exc: BaseException | None = None
        blocked_key_group: tuple[str, str] | None = None
        for idx, (client, spec) in enumerate(self._chain):
            if not self._check_entry_can_proceed(
                client, spec, idx, blocked_key_group,
                require_tool_support=require_tool_support, phase=phase,
            ):
                continue
            self._check_budget_for(spec)
            started_at = time.perf_counter()
            try:
                async with self._bulkhead.guard(client.provider_name):
                    stream = open_stream_fn(client, spec)
                    if hasattr(stream, "__await__"):
                        stream = await stream  # provider 可能返回 coroutine
                    # 应用首 Token 超时: 超时抛 FirstTokenTimeoutError 触发 fallback
                    stream = stream_with_first_token_timeout(
                        stream, self._timeout.first_token_timeout_s
                    )
                    first, iter_state = await _stream_first(stream)
                    if idx > 0:
                        logger.info(
                            "LLM %s fallback 成功: 降级到 [%s:%s] (位置=%d)",
                            phase, client.provider_name, spec.model, idx,
                        )
                    yield first
                    final_usage: dict | None = None
                    final_reason: str | None = None
                    async for chunk in _stream_iter(stream, iter_state):
                        u = extract_usage(chunk)
                        if u:
                            final_usage = u
                        fr = extract_finish_reason(chunk)
                        if fr:
                            final_reason = fr
                        yield chunk
                self._record_success(
                    client, spec, idx, final_usage, started_at,
                    finish_reason=final_reason or "stop", phase=phase,
                )
                return
            except BulkheadRejectError as e:
                last_exc = e
                self._emit_audit(
                    client, spec, idx, started_at=started_at, error="bulkhead_reject"
                )
                logger.warning(
                    "舱壁拒绝 (流式), 切下一个 provider: %s %s/%s",
                    phase, client.provider_name, spec.model,
                )
                blocked_key_group = _entry_group(spec)
            except _EmptyStream:
                last_exc = RuntimeError(f"{client.provider_name} {phase} 输出为空")
                self._record_cost_for(client, spec, None, error=True)
                self._breakers.record_failure((spec.impl, spec.api_key))
                self._emit_audit(client, spec, idx, started_at=started_at, error="empty_stream")
                blocked_key_group = _entry_group(spec)
                logger.warning(
                    "LLM %s 输出为空, 切换备用 [%s:%s] (位置=%d)",
                    phase, client.provider_name, spec.model, idx,
                )
            except FirstTokenTimeoutError as e:
                # 首 Token 超时: 不重试, 直接 fallback 到下一个 entry
                last_exc = e
                self._record_cost_for(client, spec, None, error=True)
                self._breakers.record_failure((spec.impl, spec.api_key))
                self._emit_audit(client, spec, idx, started_at=started_at, error="first_token_timeout")
                blocked_key_group = _entry_group(spec)
                logger.warning(
                    "LLM %s 首 Token 超时 (%.1fs), 切换备用 [%s:%s] (位置=%d)",
                    phase, self._timeout.first_token_timeout_s, client.provider_name, spec.model, idx,
                )
            except Exception as e:  # noqa: BLE001
                last_exc = e
                was_rate_limited = self._record_failure(
                    client, spec, idx, e, started_at, phase=phase
                )
                if not was_rate_limited:
                    blocked_key_group = _entry_group(spec)
        if last_exc is None:
            raise RuntimeError(empty_chain_error or f"Fallback 链中没有可用的 {phase} provider")
        logger.error("LLM %s fallback 链全部失败", phase)
        raise last_exc

    # ------------------------------------------------------------------
    # chat_stream: 流式
    # ------------------------------------------------------------------
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """流式: 首包前可切换, 一旦开始 yield 就锁定."""

        def open_stream(client: LLM, spec: LLMCallSpec):
            kw = _call_kwargs(spec)
            if temperature is not None:
                kw["temperature"] = temperature
            if max_tokens is not None:
                kw["max_tokens"] = max_tokens
            return client.chat_stream(
                messages,
                extra_options=_extra_options(spec, extra_options),
                **kw,
            )

        async for chunk in self._dispatch_stream(
            open_stream,
            extract_usage=lambda c: c.usage,
            extract_finish_reason=lambda c: c.finish_reason,
            phase="stream",
        ):
            yield chunk

    # ------------------------------------------------------------------
    # chat_with_tools_stream: 流式 tool calling
    # ------------------------------------------------------------------
    async def chat_with_tools_stream(
        self,
        messages: list,
        tools: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        extra_options: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict]:
        def open_stream(client: LLM, spec: LLMCallSpec):
            kw = _call_kwargs(spec)
            if temperature is not None:
                kw["temperature"] = temperature
            if max_tokens is not None:
                kw["max_tokens"] = max_tokens
            return client.chat_with_tools_stream(
                messages,
                tools,
                tool_choice=tool_choice,
                extra_options=_extra_options(spec, extra_options),
                **kw,
            )

        async for chunk in self._dispatch_stream(
            open_stream,
            extract_usage=lambda c: c.get("usage"),
            extract_finish_reason=lambda c: c.get("finish_reason"),
            require_tool_support=True,
            phase="tool_stream",
            empty_chain_error="Fallback 链中没有任何 provider 支持 tool calling",
        ):
            yield chunk


__all__ = ["ChainEntry", "LLMDispatcher"]
