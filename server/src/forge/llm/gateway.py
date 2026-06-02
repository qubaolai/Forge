"""LLMGateway: 业务层唯一对外入口.

调用路径:
    LLMRequest
      → PrePipeline (validator → rate_limit → budget → dedup → cache)
      → LLMDispatcher (router → chain 遍历 → 熔断 → 重试 → fallback)
      → Provider
      → PostPipeline (cache_write → dedup_complete → audit)
      → LLMResponse

业务层只持有 LLMGateway. 对 ReActAgent / Summarizer 等代理风格调用方,
通过 llm.binding.GatewayLLMAdapter 暴露兼容的 chat / chat_with_tools 接口.
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
    get_idempotency_store,
)
from .providers.base import ChatChunk
from .request import CostEstimate, LLMRequest, LLMResponse
from .router import (
    Router,
    RoutingRequest,
    get_default_router,
)

if TYPE_CHECKING:
    from .dispatch.dispatcher import LLMDispatcher

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# LLMGateway: 业务层唯一对外入口
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
        router: Router | None = None,
    ) -> None:
        self._settings = settings
        self._model_cache = model_cache
        self._pipeline = pipeline or self._default_pipeline()
        self._router = router or get_default_router()

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
    ) -> LLMGateway:
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
    # 共享: 非流式异常清理 (dedup 失败回滚)
    # ------------------------------------------------------------------
    async def _dedup_failure_cleanup(self, req: LLMRequest) -> None:
        if not req.idempotency_key:
            return
        try:
            await get_idempotency_store().finish_failure(req.idempotency_key)
        except Exception:  # noqa: BLE001
            logger.debug("dedup finish_failure 失败 (已忽略)", exc_info=True)

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

        try:
            dispatcher = await self._build_dispatcher_for(req)
            started_at = time.perf_counter()
            result = await dispatcher.chat(
                req.messages,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                extra_options=req.extra_options,
            )
        except BaseException:
            await self._dedup_failure_cleanup(req)
            raise
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        resp = LLMResponse(
            content=result.content,
            model=result.model,
            provider=dispatcher.primary.provider_name,
            usage=result.usage or {},
            finish_reason="stop",
            raw=result.raw,
            latency_ms=latency_ms,
            fallback_position=dispatcher.last_fallback_position,
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
        try:
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
        except BaseException:
            await self._dedup_failure_cleanup(req)
            raise
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
            fallback_position=dispatcher.last_fallback_position,
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
            # 缓存命中: 把整段 content 按 chunk 切片回放
            from .streaming import replay_as_chunks

            await self._pipeline.run_post(req, short_circuit)
            for chunk in replay_as_chunks(short_circuit.content, usage=short_circuit.usage):
                yield chunk
            return

        dispatcher = await self._build_dispatcher_for(req)
        started_at = time.perf_counter()
        final_usage: dict | None = None
        aggregated: list[str] = []  # 聚合 deltas 给 Post pipeline 缓存
        finish_reason: str | None = "stop"
        had_error = False
        try:
            async for chunk in dispatcher.chat_stream(
                req.messages,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                extra_options=req.extra_options,
            ):
                if chunk.usage:
                    final_usage = chunk.usage
                if chunk.delta:
                    aggregated.append(chunk.delta)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                yield chunk
        except BaseException:
            had_error = True
            await self._dedup_failure_cleanup(req)
            raise
        finally:
            if not had_error:
                latency_ms = (time.perf_counter() - started_at) * 1000.0
                resp = LLMResponse(
                    content="".join(aggregated),
                    model=dispatcher.primary_spec.model,
                    provider=dispatcher.primary.provider_name,
                    usage=final_usage or {},
                    finish_reason=finish_reason,
                    latency_ms=latency_ms,
                    fallback_position=dispatcher.last_fallback_position,
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
        had_error = False
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
        except BaseException:
            had_error = True
            await self._dedup_failure_cleanup(req)
            raise
        finally:
            if not had_error:
                latency_ms = (time.perf_counter() - started_at) * 1000.0
                resp = LLMResponse(
                    content="",
                    model=dispatcher.primary_spec.model,
                    provider=dispatcher.primary.provider_name,
                    usage=final_usage or {},
                    finish_reason=final_reason or "stop",
                    latency_ms=latency_ms,
                    fallback_position=dispatcher.last_fallback_position,
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
    # 内部: 路由决策 + 构造 dispatcher
    # ------------------------------------------------------------------
    async def _resolve_provider_model(
        self, req: LLMRequest
    ) -> tuple[str | None, str | None, str]:
        """解析最终 (provider, model). 返回 (provider, model, reason).

        优先级:
            1. 用户 pin (preferred_*) 双值齐全 → 直接采用, 完全跳过 Router
            2. Router 决策 (CompositeRouter, 含 RuleBased/CostAware/LatencyAware)
            3. Router 无候选 → settings.llm 默认 (兜底)
        """
        if req.preferred_provider and req.preferred_model:
            return req.preferred_provider, req.preferred_model, "user_pin"

        available = await self._build_available_candidates()
        if not available:
            # 没有候选可路由, 回 settings 默认 / 用户 pin 半值
            return (
                req.preferred_provider or (self._settings.llm.provider or None),
                req.preferred_model or (self._settings.llm.default_model or None),
                "no_available",
            )

        routing_req = RoutingRequest(
            task_type=req.task_type,
            estimated_input_tokens=req.estimated_input_tokens,
            requires_tools=req.requires_tools or bool(req.tools),
            requires_vision=req.requires_vision,
            requires_thinking=req.requires_thinking,
            user_id=req.user_id,
            preferred_provider=req.preferred_provider,
            preferred_model=req.preferred_model,
        )
        try:
            decision = self._router.route(routing_req, available)
        except Exception:  # noqa: BLE001
            logger.exception("Router 决策异常, 降级 settings.llm 默认")
            decision = None

        if decision is None:
            return (
                self._settings.llm.provider or None,
                self._settings.llm.default_model or None,
                "router_none",
            )
        return decision.provider, decision.model, decision.reason

    async def _build_available_candidates(self) -> list:
        """从 ModelConfigCache 读出全部 enabled (provider, model) 转 Candidate.

        失败 / cache 未就绪时返回空列表 (上层会回退到 settings 默认).
        """
        try:
            model_cache = self._model_cache
            if model_cache is None:
                from .model_config_cache import ModelConfigCache
                model_cache = ModelConfigCache.get_global()
            if not await model_cache.is_ready():
                return []
            providers = await model_cache.get_providers_enabled()
        except Exception:  # noqa: BLE001
            logger.debug("model_cache 不可用, 跳过 Router 候选构造", exc_info=True)
            return []

        from forge.config.domains.llm import ModelCapabilities, ModelConfig

        candidates: list = []
        for p in providers:
            provider_name = p.get("name") or ""
            if not provider_name:
                continue
            try:
                models = await model_cache.get_models(provider_name, enabled_only=True)
            except Exception:  # noqa: BLE001
                continue
            for m in models:
                cap_data = m.get("capabilities") or {}
                try:
                    capabilities = ModelCapabilities(**cap_data)
                except Exception:  # noqa: BLE001
                    capabilities = ModelCapabilities()
                mc = ModelConfig(
                    name=m.get("name") or "",
                    display_name=m.get("display_name"),
                    capabilities=capabilities,
                )
                if mc.name:
                    candidates.append((provider_name, mc))
        return candidates

    async def _build_dispatcher_for(self, req: LLMRequest) -> LLMDispatcher:
        """根据 LLMRequest 选择 provider/model, 构造 LLMDispatcher."""
        if req.task_type == "utility":
            # Utility 走 3 级回落链, 不经 Router
            return await build_utility_dispatch_chain(
                self._settings,
                provider=req.preferred_provider,
                model=req.preferred_model,
                model_cache=self._model_cache,
            )

        provider, model, reason = await self._resolve_provider_model(req)
        if reason not in ("user_pin", "no_available"):
            logger.info(
                "LLM 路由决策: provider=%s model=%s reason=%s task_type=%s",
                provider, model, reason, req.task_type,
            )
        return await build_dispatch_chain(
            self._settings,
            provider=provider,
            model=model,
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
    "get_llm_gateway",
    "reset_llm_gateway",
]
