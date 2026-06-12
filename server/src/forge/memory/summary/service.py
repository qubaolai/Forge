"""SummaryService: 摘要生成 + 持久化的可复用业务编排.

两个调用方:
    1. memory.tasks.summarize._run (Celery 异步任务)
    2. context_mgmt.SummaryCompaction (R2 inline 主动压缩)

抽出来让两边不重复, 而且主动压缩不依赖 Celery 路径.

InfrastructureError vs None 的语义:
    - 返回 None:   没历史 / 无效消息 / LLM 返回空摘要 -- 业务上正常, 不是错误
    - 抛 InfrastructureError: LLM 初始化挂 / DB upsert 挂 -- 调用方决定重试 (Celery)
                              或降级 (ContextManager 用压缩前结果)
"""

from __future__ import annotations

import logging
from typing import Literal, cast

from forge.core.types.message import Message
from forge.memory.base import Summary

logger = logging.getLogger(__name__)


class InfrastructureError(Exception):
    """LLM / DB 基础设施失败. 业务错误不走这里."""


class SummaryService:
    """无状态. 复用单例即可."""

    async def summarize_session(self, session_id: str) -> Summary | None:
        """增量滚动摘要: 旧摘要 + 水位后新消息 -> LLM 融合 -> upsert SummaryStore.

        首次 (无旧摘要) 取最近 history_limit 条全量摘要; 之后只取
        covered_until_message_id 之后的增量消息, 与旧摘要一起喂给 LLM,
        避免旧摘要被覆盖时丢失早期信息. 水位后无新消息时直接返回 None (幂等不空烧).
        增量积压超过 history_limit 时本次只消化最旧一批, 水位推进到已消化处,
        下次触发继续消化.

        Returns:
            Summary: upsert 后的最新摘要 (含 version).
            None:    水位后无新消息 / 无可摘要内容 / LLM 返回空字符串.

        Raises:
            InfrastructureError: LLM 初始化 / DB 写入失败.
        """
        from forge.config.settings import get_settings
        from forge.infrastructure.database.database import get_session_factory, init_engine
        from forge.infrastructure.database.repositories.chat_message_repo import (
            ChatMessageRepository,
        )
        from forge.llm import get_llm_gateway
        from forge.memory.summary.store import SummaryStore
        from forge.memory.summary.summarizer import Summarizer

        init_engine()  # 幂等, worker 进程也安全
        settings = get_settings()
        # factory 既喂给只读 history 查询, 也注入 SummaryStore (其内部自管会话).
        factory = get_session_factory()
        store = SummaryStore(factory)

        # 1. 取旧摘要 (增量滚动的水位); 读失败按 "无旧摘要" 处理, 退化为全量
        previous: Summary | None = None
        try:
            previous = await store.get(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取旧摘要失败 session=%s, 退化为全量摘要: %s", session_id, exc)

        # 2. 加载 history: 有旧摘要只取水位之后的增量, 否则取最近 N 条
        async with factory() as db:
            repo = ChatMessageRepository(db)
            if previous is not None and previous.covered_until_message_id:
                rows = await repo.load_after(
                    session_id,
                    previous.covered_until_message_id,
                    limit=settings.memory.summarizer.history_limit,
                )
            else:
                rows = await repo.load_recent(
                    session_id, limit=settings.memory.summarizer.history_limit
                )

        if not rows:
            logger.info("摘要跳过 session=%s 水位后无新消息", session_id)
            return None

        messages: list[Message] = []
        for row in rows:
            if row.role in ("user", "assistant") and row.content:
                role = cast(Literal["user", "assistant"], row.role)
                messages.append(Message(role=role, content=row.content))
        if not messages:
            logger.info("摘要跳过 session=%s 无有效消息", session_id)
            return None

        covered_until = rows[-1].id  # load_recent / load_after 均按 id 升序

        # 3. 构造 Summarizer (走 LLMGateway utility 档位, task_type="utility")
        try:
            provider = settings.memory.summarizer.provider or None
            model = settings.memory.summarizer.model or None
            gateway = get_llm_gateway(settings)
        except Exception as exc:
            logger.exception("Summarizer LLM 初始化失败")
            raise InfrastructureError(f"Summarizer LLM 初始化失败: {exc}") from exc

        summarizer = Summarizer(
            gateway,
            max_summary_tokens=settings.memory.summarizer.max_summary_tokens,
            preferred_provider=provider,
            preferred_model=model,
        )
        used_model = model or "<utility-routed>"

        # 4. 生成 (async, 直接 await): 旧摘要喂回 LLM, 新摘要 = 融合(旧摘要 + 增量消息)
        summary_text = await summarizer.summarize(
            messages,
            previous_summary=previous.content if previous else None,
        )
        if not summary_text:
            logger.info("摘要跳过 session=%s LLM 返回空", session_id)
            return None

        # 5. 持久化
        token_count = max(1, len(summary_text) // 2)
        try:
            summary = await store.upsert(
                session_id=session_id,
                content=summary_text,
                covered_until_message_id=covered_until,
                token_count=token_count,
            )
        except Exception as exc:  # noqa: BLE001
            raise InfrastructureError(f"SummaryStore.upsert 失败: {exc}") from exc

        logger.info(
            "摘要写入成功 session=%s content_len=%d covered_until=%s model=%s version=%d",
            session_id,
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
