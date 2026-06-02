"""prose_summarizer: 文章/普通文本段 -> 摘要 (LLM, 贵, 异步路径调用).

参照 memory/summary/summarizer.py 的写法, 但归属 context 子系统、不依赖 memory:
    - 一律走 LLMGateway (task_type="utility", model_profile="fast")。
    - prompt 模板 context/digest_prose。
    - 失败返回空串 (调用方据此决定降级为原文截断)。
"""

from __future__ import annotations

import logging

from forge.llm import LLMGateway, LLMRequest
from forge.llm.providers.base import ChatMessage
from forge.prompts import get_registry

logger = logging.getLogger(__name__)


class ProseSummarizer:
    """LLM 驱动的文本段摘要器 (无状态, 单例可复用)."""

    PROMPT_NAME = "context/digest_prose"

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        max_tokens: int = 512,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
    ) -> None:
        self._gateway = gateway
        self._max_tokens = max_tokens
        self._preferred_provider = preferred_provider
        self._preferred_model = preferred_model

    async def summarize(self, text: str) -> str:
        """把一段长文本压缩成保留关键信息的摘要 (async)。失败 / 空输入返回 ""。"""
        cleaned = (text or "").strip()
        if not cleaned:
            return ""

        prompt = get_registry().render(
            self.PROMPT_NAME,
            text=cleaned,
            max_tokens=self._max_tokens,
        )
        req = LLMRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=0.2,
            max_tokens=self._max_tokens,
            task_type="utility",
            model_profile="fast",
            preferred_provider=self._preferred_provider,
            preferred_model=self._preferred_model,
            cache_enabled=False,
        )
        try:
            resp = await self._gateway.complete(req)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ProseSummarizer LLM 调用失败: %s", exc)
            return ""
        return (resp.content or "").strip()
