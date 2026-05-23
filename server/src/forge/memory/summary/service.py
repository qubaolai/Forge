"""SummaryService: 摘要生成 + 持久化的可复用业务编排.

两个调用方:
    1. memory.tasks.summarize._run (Celery 异步任务)
    2. context.ContextAssembler.compact_and_reassemble (R2 inline 主动压缩)

抽出来让两边不重复, 而且 ContextAssembler 不依赖 Celery 路径.

InfrastructureError vs None 的语义:
    - 返回 None:   没历史 / 无效消息 / LLM 返回空摘要 -- 业务上正常, 不是错误
    - 抛 InfrastructureError: LLM 初始化挂 / DB upsert 挂 -- 调用方决定重试 (Celery)
                              或降级 (ContextAssembler 用 prev 结果)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal, cast

from forge.core.types.message import Message
from forge.memory.base import Summary

logger = logging.getLogger(__name__)


class InfrastructureError(Exception):
    """LLM / DB 基础设施失败. 业务错误不走这里."""


class SummaryService:
    """无状态. 复用单例即可."""

    async def summarize_session(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> Summary | None:
        """加载 history -> LLM 摘要 -> upsert SummaryStore.

        Returns:
            Summary: upsert 后的最新摘要 (含 version).
            None:    没历史 / 无可摘要内容 / LLM 返回空字符串.

        Raises:
            InfrastructureError: LLM 初始化 / DB 写入失败.
        """
        from config.settings import get_settings

        from forge.infrastructure.database.database import (
            get_session_factory,
            init_engine,
        )

        from forge.infrastructure.database.repositories.chat_message_repo import (
            ChatMessageRepository,
        )
        from forge.memory.summary.store import SummaryStore
        from forge.llm.gateway import build_chain_from_settings
        from forge.memory.summary.summarizer import Summarizer

        init_engine()  # 幂等, worker 进程也安全
        settings = get_settings()
        factory = get_session_factory()

        # 1. 加载 history
        async with factory() as db:
            repo = ChatMessageRepository(db)
            rows = await repo.load_recent(
                session_id, limit=settings.memory.summarizer.history_limit
            )

        if not rows:
            logger.info("摘要跳过 session=%s 无历史", session_id)
            return None

        messages: list[Message] = []
        for row in rows:
            if row.role in ("user", "assistant") and row.content:
                role = cast(Literal["user", "assistant"], row.role)
                messages.append(Message(role=role, content=row.content))
        if not messages:
            logger.info("摘要跳过 session=%s 无有效消息", session_id)
            return None

        covered_until = rows[-1].id  # load_recent 已按时间升序

        # 2. 构造 Summarizer LLM 链 (走工具模型三级回落: 任务配置 → utility_llm → 主模型)
        try:
            provider = settings.memory.summarizer.provider or None
            model = settings.memory.summarizer.model or None
            chain = await build_chain_from_settings(settings, provider=provider, model=model)
            used_model = chain.primary_spec.model
        except Exception as exc:
            raise InfrastructureError(f"Summarizer LLM 初始化失败: {exc}") from exc

        summarizer = Summarizer(
            chain, max_summary_tokens=settings.memory.summarizer.max_summary_tokens
        )

        # 3. 生成
        summary_text = await asyncio.to_thread(summarizer.summarize, messages)
        if not summary_text:
            logger.info("摘要跳过 session=%s LLM 返回空", session_id)
            return None

        # 4. 持久化
        token_count = max(1, len(summary_text) // 2)
        store = SummaryStore(factory)
        try:
            summary = await store.upsert(
                session_id=session_id,
                workspace_id=workspace_id,
                content=summary_text,
                covered_until_message_id=covered_until,
                token_count=token_count,
            )
        except Exception as exc:  # noqa: BLE001
            raise InfrastructureError(f"SummaryStore.upsert 失败: {exc}") from exc

        logger.info(
            "摘要写入成功 session=%s workspace=%s content_len=%d covered_until=%s "
            "model=%s version=%d",
            session_id,
            workspace_id or "<none>",
            len(summary_text),
            covered_until,
            used_model,
            summary.version,
        )
        return summary


# 默认单例
_DEFAULT_SERVICE: SummaryService | None = None


def get_summary_service() -> SummaryService:
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = SummaryService()
    return _DEFAULT_SERVICE
