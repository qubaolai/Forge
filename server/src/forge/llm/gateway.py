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

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import jsonschema

from forge.core.types.message import Message

from .dispatch.chain_builder import build_dispatch_chain
from .dispatch.chain_resolver import resolve_chain
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

if TYPE_CHECKING:
    from .dispatch.dispatcher import LLMDispatcher

logger = logging.getLogger(__name__)


class StructuredOutputError(Exception):
    """结构化输出在重试耗尽后仍不符合 schema."""

    def __init__(self, message: str, *, last_content: str) -> None:
        super().__init__(message)
        self.last_content = last_content


def _validate_structured(content: str, schema: dict[str, Any]) -> str | None:
    """校验 content 是否为符合 schema 的合法 JSON.

    合规返回 None, 否则返回错误描述 (用于回灌纠正提示).
    """
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        return f"不是合法 JSON ({e})"
    try:
        jsonschema.validate(instance=parsed, schema=schema)
    except jsonschema.ValidationError as e:
        return f"不符合 schema: {e.message}"
    return None


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
    # 结构化输出 (JSON Schema 约束 + 校验 + 回灌重试)
    # ------------------------------------------------------------------
    async def complete_structured(
        self,
        req: LLMRequest,
        *,
        schema: dict[str, Any],
        name: str = "response",
        strict: bool = True,
        max_retries: int = 2,
    ) -> LLMResponse:
        """结构化输出 facade.

        - 把 provider 无关的结构化意图塞进 extra_options['structured_output'],
          由各 provider 的 build_structured_options 翻译成原生参数 (openai 系 →
          response_format); 不支持原生约束的 provider (google/anthropic) 靠下面的
          回灌重试兜底.
        - 用 jsonschema 校验返回内容; 非法/不合规则把错误输出回灌并要求模型纠正后重试.
        - 成功返回那次 LLMResponse 原样 (content 为合法 JSON 字符串, 调用方自行
          json.loads). max_retries 次纠正后仍不合规, 抛 StructuredOutputError.
        """
        base_extra = dict(req.extra_options or {})
        base_extra["structured_output"] = {
            "schema": schema,
            "name": name,
            "strict": strict,
        }

        messages = list(req.messages)  # 拷贝, 不修改入参
        last_content = ""
        last_err = ""
        for attempt in range(max_retries + 1):
            # 重试轮换幂等键, 否则 dedup 中间件会短路返回上一轮错误结果
            idem = req.idempotency_key
            if idem and attempt > 0:
                idem = f"{idem}:structured:{attempt}"
            attempt_req = replace(
                req,
                messages=messages,
                extra_options=base_extra,
                idempotency_key=idem,
            )
            resp = await self.complete(attempt_req)
            last_content = resp.content or ""
            err = _validate_structured(last_content, schema)
            if err is None:
                return resp
            last_err = err
            if attempt >= max_retries:
                break
            # 回灌: 上一轮错误输出 (assistant) + 纠正提示 (user), 再次请求
            messages = messages + [
                Message(role="assistant", content=last_content),
                Message(
                    role="user",
                    content=(
                        f"你上一次的输出不符合要求: {err}. 请严格只输出符合给定 "
                        "JSON Schema 的合法 JSON, 不要包含任何解释、Markdown 代码块"
                        "标记或多余文字."
                    ),
                ),
            ]
        raise StructuredOutputError(
            f"结构化输出重试 {max_retries} 次后仍不合规: {last_err}",
            last_content=last_content,
        )

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
    # 内部: 解析模型链 + 构造 dispatcher
    # ------------------------------------------------------------------
    def _resolve_model_cache(self):
        if self._model_cache is not None:
            return self._model_cache
        from .model_config_cache import ModelConfigCache
        return ModelConfigCache.get_global()

    async def _build_dispatcher_for(self, req: LLMRequest) -> LLMDispatcher:
        """解析有序模型链(user_pin > 档位链 > 系统保底)并构造 LLMDispatcher.

        选择逻辑统一在 chain_resolver.resolve_chain;utility 不再有独立分支
        (调用方传 model_profile="fast" 即走 fast 档位链)。
        """
        model_cache = self._resolve_model_cache()
        chain = await resolve_chain(req, self._settings, model_cache)
        return await build_dispatch_chain(
            self._settings,
            chain=chain,
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
