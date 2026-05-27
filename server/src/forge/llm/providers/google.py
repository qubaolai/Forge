"""Google Gemini LLM 实现 (google-genai SDK).

google-generativeai (旧包) 2025 年起停止维护, 已迁移到 google-genai.
新 SDK 把 client 全局化, 内部按 model 路由, 不需要按 model 缓存
GenerativeModel 对象; system prompt 走 config.system_instruction.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, cast

from google import genai
from google.genai import types as genai_types

from ..registry import register_llm
from .base import LLM, ChatChunk, ChatMessage

logger = logging.getLogger(__name__)


@register_llm("google")
class GoogleLLM(LLM):
    """Google Gemini provider."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,  # google-genai 公开 API 暂不支持 base_url 自定义, 忽略
        timeout: float = 30.0,
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout)
        # google-genai 的 Client 内部按 model 路由, 不再需要逐 model 缓存
        # GenerativeModel 对象, 单 client 全局复用.
        self._client = genai.Client(api_key=api_key)
        logger.info("Google LLM 就绪: key=%s", self.api_key_fingerprint)

    @property
    def provider_name(self) -> str:
        return "google"

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
        top_p: float | None = None,
        top_k: int | None = None,
        extra_options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        contents, system_instruction = self._convert_messages(messages)
        config = self._build_gen_config(temperature, max_tokens, top_p, top_k, system_instruction)
        try:
            generate_content_stream = cast(
                Any,
                self._client.models.generate_content_stream,
            )
            stream = generate_content_stream(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            raise RuntimeError(f"Google LLM 流式调用失败: {e}") from e

        usage: dict = {}
        for chunk in stream:
            if chunk.text:
                yield ChatChunk(delta=chunk.text, finish_reason=None)
            # 每个 chunk 都可能带 usage_metadata, 取最后一个非空的
            if chunk.usage_metadata is not None:
                usage = {
                    "prompt_token_count": chunk.usage_metadata.prompt_token_count,
                    "candidates_token_count": chunk.usage_metadata.candidates_token_count,
                }
        yield ChatChunk(delta="", finish_reason="stop", usage=usage)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _build_gen_config(
        temperature: float | None,
        max_tokens: int | None,
        top_p: float | None,
        top_k: int | None,
        system_instruction: str | None,
    ) -> genai_types.GenerateContentConfig:
        return genai_types.GenerateContentConfig(
            temperature=temperature if temperature is not None else 1.0,
            max_output_tokens=max_tokens,
            top_p=top_p,
            top_k=top_k,
            system_instruction=system_instruction,
        )

    @staticmethod
    def _convert_messages(
        messages: list[ChatMessage],
    ) -> tuple[list[genai_types.Content], str | None]:
        """转 ChatMessage -> (contents, system_instruction).

        google-genai 推荐把 system prompt 放在 config.system_instruction
        而不是拼到第一条 user 消息. 多条 system 用换行拼接.
        history 用 user / model 角色交替 (Gemini 不识别 'assistant').
        """
        system_parts: list[str] = []
        contents: list[genai_types.Content] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            elif m.role == "user":
                contents.append(
                    genai_types.Content(role="user", parts=[genai_types.Part(text=m.content)])
                )
            elif m.role == "assistant":
                contents.append(
                    genai_types.Content(role="model", parts=[genai_types.Part(text=m.content)])
                )
        system_instruction = "\n".join(system_parts) if system_parts else None
        return contents, system_instruction
