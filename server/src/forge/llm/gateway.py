"""LLMGateway: 业务层唯一对外入口.

调用路径:
    LLMRequest
      → PrePipeline (validator → budget → 后续 Phase 加入 rate_limit/dedup/cache)
      → LLMDispatcher (router → chain 遍历 → 熔断 → 重试 → fallback)
      → Provider
      → PostPipeline (audit; 后续 Phase 加入 cache_write)
      → LLMResponse

旧函数 (build_chain_from_settings / register_llm 等) 作为重导出保留向后兼容.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from .dispatch.chain_builder import build_dispatch_chain, build_utility_dispatch_chain
from .pipeline import (
    AuditMiddleware,
    BudgetMiddleware,
    CacheWriteMiddleware,
    DedupCompleteMiddleware,
    DeduplicationMiddleware,
    ExactCacheMiddleware,
    InboundRateLimitMiddleware,
    InputValidatorMiddleware,
    PipelineRunner,
    PostMiddleware,
    PreMiddleware,
)
from .providers.base import ChatChunk
from .registry import (
    build_llm_client,
    list_providers,
    register_llm,
    split_provider_model,
)
from .request import CostEstimate, LLMRequest, LLMResponse

if TYPE_CHECKING:
    from .dispatch.dispatcher import LLMDispatcher
    from .router import RoutingRequest

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 旧接口重导出 (向后兼容)
# ----------------------------------------------------------------------
async def build_chain_from_settings(
    settings,
    *,
    provider: str | None = None,
    model: str | None = None,
    routing_request: "RoutingRequest | None" = None,
    model_cache=None,
):
    """向后兼容: 走新 dispatch.chain_builder.build_dispatch_chain.

    routing_request 暂时忽略 (Phase 5 智能路由接入).
    """
    if routing_request is not None:
        logger.warning("LLM 路由请求已忽略: 当前主模型必须显式传入 provider/model")
    return await build_dispatch_chain(
        settings,
        provider=provider,
        model=model,
        model_cache=model_cache,
    )


async def build_utility_chain_from_settings(
    settings,
    *,
    utility_provider: str | None = None,
    utility_model: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    model_cache=None,
):
    """向后兼容: 走新 dispatch.chain_builder.build_utility_dispatch_chain."""
    return await build_utility_dispatch_chain(
        settings,
        utility_provider=utility_provider,
        utility_model=utility_model,
        provider=provider,
        model=model,
        model_cache=model_cache,
    )


# ----------------------------------------------------------------------
# LLMGateway: 新对外入口
# ----------------------------------------------------------------------
class LLMGateway:
    """LLM 网关统一对外接口.

    业务层只依赖此类, 不感知 dispatcher / chain / provider 等内部概念.
    """

    def __init__(
        self,
        settings,
        model_cache=None,
        *,
        pipeline: PipelineRunner | None = None,
    ) -> None:
        self._settings = settings
        self._model_cache = model_cache
        self._pipeline = pipeline or self._default_pipeline()

    # ------------------------------------------------------------------
    # 工厂
    # ------------------------------------------------------------------
    @classmethod
    async def from_settings(
        cls,
        settings,
        model_cache=None,
        *,
        pre_middlewares: list[PreMiddleware] | None = None,
        post_middlewares: list[PostMiddleware] | None = None,
    ) -> "LLMGateway":
        """构建 LLMGateway. 不传 middleware 则使用默认 Pre/Post 链."""
        pipeline = PipelineRunner(
            pre_middlewares=pre_middlewares,
            post_middlewares=post_middlewares,
        ) if pre_middlewares is not None or post_middlewares is not None else None
        return cls(settings, model_cache=model_cache, pipeline=pipeline)

    @staticmethod
    def _default_pipeline() -> PipelineRunner:
        """默认 Pipeline.

        Pre (按短路优先顺序):
            validator → rate_limit → budget → dedup → cache
        Post (每个都执行):
            cache_write → dedup_complete → audit
        """
        return PipelineRunner(
            pre_middlewares=[
                InputValidatorMiddleware(),
                InboundRateLimitMiddleware(),
                BudgetMiddleware(),
                DeduplicationMiddleware(),
                ExactCacheMiddleware(),
            ],
            post_middlewares=[
                CacheWriteMiddleware(),
                DedupCompleteMiddleware(),
                AuditMiddleware(),
            ],
        )

    @property
    def pipeline(self) -> PipelineRunner:
        return self._pipeline

    # ------------------------------------------------------------------
    # 非流式 chat
    # ------------------------------------------------------------------
    async def complete(self, req: LLMRequest) -> LLMResponse:
        """非流式 chat. 若 req.tools 非空自动走 chat_with_tools."""
        short_circuit = await self._pipeline.run_pre(req)
        if short_circuit is not None:
            return await self._pipeline.run_post(req, short_circuit)

        if req.tools:
            return await self._complete_with_tools_after_pre(req)

        dispatcher = await self._build_dispatcher_for(req)
        started_at = time.perf_counter()
        result = await dispatcher.chat(
            req.messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            extra_options=req.extra_options,
        )
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        resp = LLMResponse(
            content=result.content,
            model=result.model,
            provider=dispatcher.primary.provider_name,
            usage=result.usage or {},
            finish_reason="stop",
            raw=result.raw,
            latency_ms=latency_ms,
        )
        return await self._pipeline.run_post(req, resp)

    # ------------------------------------------------------------------
    # 非流式 tool calling
    # ------------------------------------------------------------------
    async def complete_with_tools(self, req: LLMRequest) -> LLMResponse:
        if not req.tools:
            raise ValueError("complete_with_tools 需要 req.tools 非空")
        short_circuit = await self._pipeline.run_pre(req)
        if short_circuit is not None:
            return await self._pipeline.run_post(req, short_circuit)
        return await self._complete_with_tools_after_pre(req)

    async def _complete_with_tools_after_pre(self, req: LLMRequest) -> LLMResponse:
        dispatcher = await self._build_dispatcher_for(req)
        started_at = time.perf_counter()
        result = await dispatcher.chat_with_tools(
            req.messages,
            req.tools or [],
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            tool_choice=req.tool_choice,
            extra_options=req.extra_options,
        )
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        resp = LLMResponse(
            content=result.get("content", ""),
            model=result.get("model", "") or dispatcher.primary_spec.model,
            provider=dispatcher.primary.provider_name,
            usage=result.get("usage") or {},
            finish_reason=result.get("finish_reason")
            or ("tool_calls" if result.get("tool_calls") else "stop"),
            tool_calls=result.get("tool_calls"),
            raw=result,
            latency_ms=latency_ms,
        )
        return await self._pipeline.run_post(req, resp)

    # ------------------------------------------------------------------
    # 流式 chat
    # ------------------------------------------------------------------
    async def stream(self, req: LLMRequest) -> AsyncIterator[ChatChunk]:
        if req.tools:
            raise ValueError("流式 tool calling 请使用 stream_with_tools")
        short_circuit = await self._pipeline.run_pre(req)
        if short_circuit is not None:
            # 缓存命中: 把响应转换为单一最终 chunk 输出
            await self._pipeline.run_post(req, short_circuit)
            yield ChatChunk(delta=short_circuit.content, finish_reason="stop")
            return
        dispatcher = await self._build_dispatcher_for(req)
        started_at = time.perf_counter()
        final_usage: dict | None = None
        try:
            async for chunk in dispatcher.chat_stream(
                req.messages,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                extra_options=req.extra_options,
            ):
                if chunk.usage:
                    final_usage = chunk.usage
                yield chunk
        finally:
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            resp = LLMResponse(
                content="",  # 流式不缓存聚合 content (Phase 4 流式缓存 P3)
                model=dispatcher.primary_spec.model,
                provider=dispatcher.primary.provider_name,
                usage=final_usage or {},
                finish_reason="stop",
                latency_ms=latency_ms,
            )
            try:
                await self._pipeline.run_post(req, resp)
            except Exception:  # noqa: BLE001
                logger.exception("流式 Post pipeline 异常 (已忽略)")

    async def stream_with_tools(self, req: LLMRequest) -> AsyncIterator[dict[str, Any]]:
        if not req.tools:
            raise ValueError("stream_with_tools 需要 req.tools 非空")
        short_circuit = await self._pipeline.run_pre(req)
        if short_circuit is not None:
            await self._pipeline.run_post(req, short_circuit)
            yield {
                "content_delta": short_circuit.content,
                "tool_calls": short_circuit.tool_calls or [],
                "finish_reason": short_circuit.finish_reason or "stop",
                "usage": short_circuit.usage or {},
                "model": short_circuit.model,
            }
            return
        dispatcher = await self._build_dispatcher_for(req)
        started_at = time.perf_counter()
        final_usage: dict | None = None
        final_reason: str | None = None
        try:
            async for chunk in dispatcher.chat_with_tools_stream(
                req.messages,
                req.tools or [],
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                tool_choice=req.tool_choice,
                extra_options=req.extra_options,
            ):
                if chunk.get("usage"):
                    final_usage = chunk["usage"]
                if chunk.get("finish_reason"):
                    final_reason = chunk["finish_reason"]
                yield chunk
        finally:
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            resp = LLMResponse(
                content="",
                model=dispatcher.primary_spec.model,
                provider=dispatcher.primary.provider_name,
                usage=final_usage or {},
                finish_reason=final_reason or "stop",
                latency_ms=latency_ms,
            )
            try:
                await self._pipeline.run_post(req, resp)
            except Exception:  # noqa: BLE001
                logger.exception("流式 Post pipeline 异常 (已忽略)")

    # ------------------------------------------------------------------
    # 成本估算 (不调用 LLM)
    # ------------------------------------------------------------------
    async def estimate_cost(self, req: LLMRequest) -> CostEstimate:
        """成本预估: 不实际调用 LLM, 基于 token_counter + cost_tracker 价格表."""
        from .cost_tracker import estimate_cost as _estimate_cost
        from .token_counter import get_token_counter

        provider = req.preferred_provider or self._settings.llm.provider
        model = req.preferred_model or self._settings.llm.default_model
        if not provider or not model:
            raise ValueError("estimate_cost 需要显式 provider/model")

        if req.estimated_input_tokens:
            prompt_tokens = req.estimated_input_tokens
        else:
            counter = get_token_counter()
            prompt_tokens = sum(counter.count_text(m.content or "") for m in req.messages)
        output_tokens = req.max_tokens or 1024
        cost = _estimate_cost(model, prompt_tokens, output_tokens)
        return CostEstimate(
            estimated_input_tokens=prompt_tokens,
            estimated_output_tokens=output_tokens,
            estimated_usd=cost,
            model=model,
            provider=provider,
        )

    # ------------------------------------------------------------------
    # 内部: 构造 dispatcher
    # ------------------------------------------------------------------
    async def _build_dispatcher_for(self, req: LLMRequest) -> "LLMDispatcher":
        """根据 LLMRequest 选择 provider/model, 构造 LLMDispatcher.

        Phase 2: preferred_provider/model 显式 pin; task_type=utility 走 utility chain;
        其他情况走 settings.llm 默认值. Phase 5 引入智能 Router.
        """
        provider = req.preferred_provider
        model = req.preferred_model

        if req.task_type == "utility":
            return await build_utility_dispatch_chain(
                self._settings,
                provider=provider,
                model=model,
                model_cache=self._model_cache,
            )

        return await build_dispatch_chain(
            self._settings,
            provider=provider or self._settings.llm.provider or None,
            model=model or self._settings.llm.default_model or None,
            model_cache=self._model_cache,
        )


# ----------------------------------------------------------------------
# 全局单例 (Phase 2: 工厂方法, 后续可接入 DI 容器)
# ----------------------------------------------------------------------
_gateway_instance: LLMGateway | None = None


def get_llm_gateway(settings=None, model_cache=None) -> LLMGateway:
    """获取全局 LLMGateway 实例.

    Phase 2: 进程内单例 (settings 用首次传入的). 后续接入 FastAPI DI 容器.
    """
    global _gateway_instance
    if _gateway_instance is None:
        if settings is None:
            raise ValueError("首次构造 LLMGateway 必须传入 settings")
        _gateway_instance = LLMGateway(settings, model_cache=model_cache)
    return _gateway_instance


def reset_llm_gateway() -> None:
    """测试用: 重置全局单例."""
    global _gateway_instance
    _gateway_instance = None


__all__ = [
    "LLMGateway",
    "build_chain_from_settings",
    "build_llm_client",
    "build_utility_chain_from_settings",
    "get_llm_gateway",
    "list_providers",
    "register_llm",
    "reset_llm_gateway",
    "split_provider_model",
]
