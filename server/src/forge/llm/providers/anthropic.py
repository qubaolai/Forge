"""Anthropic Claude LLM 实现.

一个 LLM 实例 = 一个 (anthropic, api_key) 的 SDK client.
model 在每次调用时通过 model 参数传入.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, cast

import anthropic

from ..caching.prompt_cache import build_anthropic_system
from ..gateway import register_llm
from .base import LLM, ChatChunk, ChatMessage

logger = logging.getLogger(__name__)


@register_llm("anthropic")
class AnthropicLLM(LLM):
    """Anthropic Claude provider."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout)
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        if timeout:
            client_kwargs["timeout"] = timeout
        self._client = anthropic.Anthropic(**client_kwargs)
        logger.info("Anthropic LLM 就绪: key=%s", self.api_key_fingerprint)

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def supports_native_stream(self) -> bool:
        return True

    def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        thinking_budget: int | None = None,
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatChunk]:
        system_text, msgs = self._split_system(messages)
        kw = self._build_kwargs(
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            thinking_budget=thinking_budget,
            extra_options=extra_options,
        )
        # 系统提示词够长时打 cache_control={ephemeral} 标记, 让 Anthropic 服务端
        # 做 KV 缓存; 命中后 prompt token 走 10% 价格.
        system = build_anthropic_system(system_text)
        try:
            message_stream = cast(Any, self._client.messages.stream)
            with message_stream(
                model=model,
                system=system,
                messages=msgs,
                **kw,
            ) as stream:
                for text in stream.text_stream:
                    yield ChatChunk(delta=text, finish_reason=None)
                final = stream.get_final_message()
                usage = {
                    "input_tokens": final.usage.input_tokens,
                    "output_tokens": final.usage.output_tokens,
                }
                # 命中 / 创建 cache 时附加字段, 不命中时为 0
                cache_read = getattr(final.usage, "cache_read_input_tokens", None)
                cache_create = getattr(final.usage, "cache_creation_input_tokens", None)
                if cache_read is not None:
                    usage["cache_read_input_tokens"] = int(cache_read or 0)
                if cache_create is not None:
                    usage["cache_creation_input_tokens"] = int(cache_create or 0)
                yield ChatChunk(
                    delta="",
                    finish_reason=final.stop_reason or "stop",
                    usage=usage,
                )
        except anthropic.APIError as e:
            raise RuntimeError(f"Anthropic 流式调用失败: {e}") from e

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _split_system(
        messages: list[ChatMessage],
    ) -> tuple[str, list[dict]]:
        """把第一条 system 消息单独提取, 其余转成 Anthropic messages 格式."""
        system = ""
        rest: list[dict] = []
        for m in messages:
            if m.role == "system" and not system:
                system = m.content
            else:
                rest.append({"role": m.role, "content": m.content})
        return system, rest

    def _build_kwargs(
        self,
        *,
        temperature: float | None,
        max_tokens: int | None,
        thinking: bool | None = None,
        thinking_budget: int | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "temperature": temperature if temperature is not None else 1.0,
            "max_tokens": max_tokens if max_tokens is not None else 4096,
        }
        # thinking 只从 extra_options 读取 (per-request 前端传入)
        opts = extra_options or {}
        effective_thinking = opts.get("thinking")
        if effective_thinking:
            budget = opts.get("thinking_budget") or thinking_budget or 5000
            kw["thinking"] = {"type": "enabled", "budget_tokens": int(budget)}
            kw["temperature"] = 1.0
        return kw
