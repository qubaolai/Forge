"""Summarizer: LLM 驱动的会话摘要生成.

调用方 (Celery worker) 一次性给一段 history, 生成一段摘要文本.
本类不碰 DB / 不碰 Store; 只做 "list[Message] -> 摘要字符串" 这一件事.
持久化由调用方自己 upsert.

LLM 配置:
    走 settings.memory.summarizer.provider/model. 留空时 fallback 到默认 LLM.
    用同一个 LLMManager 池化, 不会每次新建 HTTP client.
"""

from __future__ import annotations

import logging
from typing import Protocol

from forge.core.types.message import Message
from forge.llm.providers.base import ChatMessage, ChatResult
from forge.prompts import get_registry

logger = logging.getLogger(__name__)


class _ChatLLM(Protocol):
    """Summarizer 用到的 LLM 子集: 只要一个 chat()."""

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult: ...


class Summarizer:
    """LLM 驱动的会话摘要器.

    Note:
        sync 调用 (LLM.chat 本身就是 sync). Celery 任务函数也是 sync, 直接调.
        在 async 上下文里要用就 await asyncio.to_thread(summarizer.summarize, ...).
    """

    PROMPT_NAME = "memory/summarize"

    def __init__(
        self,
        llm: _ChatLLM,
        *,
        max_summary_tokens: int = 1500,
    ) -> None:
        self._llm = llm
        self._max_tokens = max_summary_tokens

    def summarize(self, messages: list[Message]) -> str:
        """把一段对话压缩成摘要.

        Args:
            messages: 完整对话 (user/assistant). system 消息会被过滤掉.

        Returns:
            摘要正文 (可能为空字符串, 表示对话太稀薄无可摘要).
            出错时返回空字符串 + 日志, 不抛 -- 调用方据此判断要不要 upsert.
        """
        cleaned = [m for m in messages if m.role in ("user", "assistant") and m.content]
        if not cleaned:
            return ""

        prompt = get_registry().render(
            self.PROMPT_NAME,
            messages=cleaned,
            max_tokens=self._max_tokens,
        )

        try:
            result = self._llm.chat(
                [ChatMessage(role="user", content=prompt)],
                temperature=0.3,
                max_tokens=self._max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Summarizer LLM 调用失败: %s", exc)
            return ""

        return (result.content or "").strip()
