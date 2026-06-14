"""GatewayBinding + GatewayLLMAdapter: 业务层接入 LLMGateway 的桥.

为什么需要这层:
    LLMGateway 的接口是 (LLMRequest → LLMResponse), 但 ReActAgent / Summarizer
    等"代理风格"调用方期望的接口是 (messages → result), 内含 user_id / provider
    / model 等绑定信息. 直接让它们构造 LLMRequest 会把网关上下文渗透进 Agent 层.

    GatewayBinding 把"网关 + 调用上下文"打包成一个轻量对象,
    GatewayLLMAdapter 暴露 chat / chat_stream / chat_with_tools /
    chat_with_tools_stream 四个方法, 内部全部走 gateway.complete* 系列,
    所有 Pre/Post middleware (限流 / 预算 / 缓存 / 审计) 自动生效.

调用边界:
    业务层 (chat orchestrator) 构造 GatewayBinding,
    然后 ReActAgent(llm=GatewayLLMAdapter(binding)). Agent 层不再感知 gateway.

    对于一次性 utility 调用 (摘要 / 标题生成), 直接用
    LLMGateway.complete(LLMRequest(task_type="utility", ...)).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from forge.core.types.message import Message

from .contracts import ToolCallingLLM
from .gateway import LLMGateway
from .providers.base import ChatChunk, ChatMessage, ChatResult
from .request import LLMRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GatewayBinding:
    """网关调用上下文绑定. 一个 Agent / Runner 对应一份, 不可变.

    Fields:
        gateway: 共用单例
        user_id: 限额/成本归属
        preferred_provider/preferred_model: 用户 pin (前端选定); 双值齐全则 Router 短路
        model_profile: 系统路由档位 (fast/smart/strong); preferred_* 优先
        task_type: chat | tool_use | utility | summary | embedding
        cache_enabled: 精确缓存开关
        extra_options: 透传给 provider SDK 的额外参数 (DB ModelConfig.extra 与 LLMCallSpec 合并)
    """

    gateway: LLMGateway
    user_id: str | None = None
    preferred_provider: str | None = None
    preferred_model: str | None = None
    model_profile: str | None = None
    task_type: str = "chat"
    cache_enabled: bool = True
    extra_options: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def make_request(
        self,
        messages: list[ChatMessage] | list[Message],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        requires_thinking: bool = False,
        requires_vision: bool = False,
    ) -> LLMRequest:
        """组装 LLMRequest, binding 字段为基底, 函数参数覆盖."""
        merged_extra = dict(self.extra_options or {})
        if extra_options:
            merged_extra.update(extra_options)
        return LLMRequest(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_options=merged_extra or None,
            user_id=self.user_id,
            preferred_provider=self.preferred_provider,
            preferred_model=self.preferred_model,
            model_profile=self.model_profile,
            task_type=self.task_type,
            cache_enabled=self.cache_enabled,
            idempotency_key=idempotency_key,
            requires_thinking=requires_thinking,
            requires_vision=requires_vision,
            requires_tools=bool(tools),
            extra=dict(self.extra),
        )


class GatewayLLMAdapter(ToolCallingLLM):
    """适配 ToolCallingLLM 协议, 让 ReActAgent / Summarizer 透明走 LLMGateway.

    暴露 4 个方法 (与原 LLMDispatcher / LLMFallbackChain 同名):
        - chat(messages, ...)                       → gateway.complete
        - chat_stream(messages, ...)                → gateway.stream
        - chat_with_tools(messages, tools, ...)     → gateway.complete_with_tools
        - chat_with_tools_stream(messages, tools, ...) → gateway.stream_with_tools

    所有调用最终都进入 Pre/Post pipeline, 享受限流 / 预算 / 缓存 / 幂等 / 审计.
    """

    def __init__(self, binding: GatewayBinding) -> None:
        self._binding = binding

    @property
    def binding(self) -> GatewayBinding:
        return self._binding

    @property
    def supports_tool_calling(self) -> bool:
        """对外保留属性, 兼容 ReActRunner 的运行时探测; 一律为 True (gateway 路由会挑能力)."""
        return True

    # ------------------------------------------------------------------
    # 非流式 chat
    # ------------------------------------------------------------------
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ChatResult:
        req = self._binding.make_request(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_options=extra_options,
            idempotency_key=idempotency_key,
        )
        resp = await self._binding.gateway.complete(req)
        return ChatResult(
            content=resp.content,
            model=resp.model,
            usage=resp.usage or {},
            raw=resp.raw,
        )

    # ------------------------------------------------------------------
    # 流式 chat
    # ------------------------------------------------------------------
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatChunk]:
        req = self._binding.make_request(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_options=extra_options,
        )
        async for chunk in self._binding.gateway.stream(req):
            yield chunk

    # ------------------------------------------------------------------
    # 非流式 tool calling
    # ------------------------------------------------------------------
    async def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        extra_options: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        req = self._binding.make_request(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_options=extra_options,
            idempotency_key=idempotency_key,
        )
        resp = await self._binding.gateway.complete_with_tools(req)
        return {
            "content": resp.content,
            "tool_calls": resp.tool_calls or [],
            "usage": resp.usage or {},
            "model": resp.model,
            "finish_reason": resp.finish_reason or "stop",
            "raw": resp.raw,
        }

    # ------------------------------------------------------------------
    # 流式 tool calling
    # ------------------------------------------------------------------
    async def chat_with_tools_stream(
        self,
        messages: list[Message],
        tools: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        extra_options: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        req = self._binding.make_request(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_options=extra_options,
        )
        async for chunk in self._binding.gateway.stream_with_tools(req):
            yield chunk


__all__ = ["GatewayBinding", "GatewayLLMAdapter"]
