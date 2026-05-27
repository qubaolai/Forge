"""Mock LLM, 不依赖外部服务, 用于测试.

注册了两个 provider:
    - mock:        默认 chat_stream 实现 (一次性 yield), 验证没真流式的 provider
    - mock_stream: override chat_stream 字符级模拟真流式
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from ..registry import register_llm
from .base import LLM, ChatChunk, ChatMessage

logger = logging.getLogger(__name__)


def _build_response(template: str, model: str, messages: list[ChatMessage]) -> str:
    last_user = next(
        (m.content for m in reversed(messages) if m.role == "user"),
        "",
    )
    return template.format(model=model, last_user_message=last_user)


@register_llm("mock")
class MockLLM(LLM):
    """非流式 mock: chat_stream 整段一次性 yield."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        response_template: str = "[{model}] echo: {last_user_message}",
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout)
        self._template = response_template
        logger.info("Mock LLM 就绪: key=%s", self.api_key_fingerprint)

    @property
    def provider_name(self) -> str:
        return "mock"

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        content = _build_response(self._template, model, messages)
        yield ChatChunk(
            delta=content,
            finish_reason="stop",
            usage={"prompt_tokens": len(messages), "completion_tokens": len(content)},
        )


@register_llm("mock_stream")
class MockStreamLLM(LLM):
    """真流式 mock: chat_stream 字符级吐增量."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        response_template: str = "[{model}] streaming echo: {last_user_message}",
        chunk_delay: float = 0.0,
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout)
        self._template = response_template
        self._chunk_delay = chunk_delay
        logger.info("MockStream LLM 就绪: key=%s", self.api_key_fingerprint)

    @property
    def provider_name(self) -> str:
        return "mock_stream"

    @property
    def supports_native_stream(self) -> bool:
        return True

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        full = _build_response(self._template, model, messages)
        for ch in full:
            if self._chunk_delay > 0:
                await asyncio.sleep(self._chunk_delay)
            yield ChatChunk(delta=ch, finish_reason=None)

        yield ChatChunk(
            delta="",
            finish_reason="stop",
            usage={
                "prompt_tokens": len(messages),
                "completion_tokens": len(full),
            },
        )
