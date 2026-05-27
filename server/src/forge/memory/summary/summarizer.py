"""Summarizer: LLM 驱动的会话摘要生成.

调用方 (Celery worker) 一次性给一段 history, 生成一段摘要文本.
本类不碰 DB / 不碰 Store; 只做 "list[Message] -> 摘要字符串" 这一件事.
持久化由调用方自己 upsert.

LLM 接入:
    一律走 LLMGateway (task_type="utility"), 通过 settings.memory.summarizer.provider/model
    设置 utility 档位的具体模型. Pre/Post pipeline (限流 / 预算 / 缓存 / 审计) 自动生效.
"""

from __future__ import annotations

import logging

from forge.core.types.message import Message
from forge.llm import LLMGateway, LLMRequest
from forge.llm.providers.base import ChatMessage
from forge.prompts import get_registry

logger = logging.getLogger(__name__)


class Summarizer:
    """LLM 驱动的会话摘要器.

    Note:
        async 接口 (summarize). Celery 任务里用 asyncio.run / 已有 event loop 时 await.
    """

    PROMPT_NAME = "memory/summarize"

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        max_summary_tokens: int = 1500,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
    ) -> None:
        self._gateway = gateway
        self._max_tokens = max_summary_tokens
        self._preferred_provider = preferred_provider
        self._preferred_model = preferred_model

    async def summarize(self, messages: list[Message]) -> str:
        """把一段对话压缩成摘要 (async).

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

        req = LLMRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=0.3,
            max_tokens=self._max_tokens,
            task_type="utility",
            model_profile="fast",
            preferred_provider=self._preferred_provider,
            preferred_model=self._preferred_model,
            cache_enabled=False,  # 摘要内容用户感知, 不走精确缓存
        )
        try:
            resp = await self._gateway.complete(req)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Summarizer LLM 调用失败: %s", exc)
            return ""

        return (resp.content or "").strip()
