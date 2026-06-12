"""FactExtractionService: 事实抽取 + 水位推进的业务编排.

流程 (镜像 SummaryService 结构):
    读水位 -> load_after 取新消息 -> 渲染 prompt -> LLM 结构化输出
    -> 逐条 FactStore.write (经 ConflictResolver) -> 推进水位

水位语义:
    "该消息及之前的内容已被抽取过". 无论写入结果是 Insert 还是 Skip
    (去重) 都推进水位 -- 否则同一段对话每 N 轮重复送 LLM 白烧成本.
    LLM 结构化输出多次纠正仍不合规 (StructuredOutputError) 同样推进:
    重试大概率仍失败, 跳过这一段更省钱.

InfrastructureError vs 正常返回:
    - 返回 int (写入条数, 可为 0): 业务正常 (无新消息 / 无可抽 / 全部去重)
    - 抛 InfrastructureError: LLM 初始化挂 / DB 读写挂 -- Celery 据此重试,
      此时水位不推进, 重试会重新处理同一段消息 (幂等: 去重兜底).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from forge.core.types.message import Message
from forge.memory.scope import MemoryScope
from forge.memory.summary.service import InfrastructureError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from forge.config.domains.memory import MemoryFactsSettings
    from forge.memory.facts.store import FactStore

logger = logging.getLogger(__name__)

# LLM 结构化输出 schema: {"facts": ["...", ...]}
_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["facts"],
}

PROMPT_NAME = "memory/extract_facts"


def build_fact_store(
    session_factory: async_sessionmaker[AsyncSession],
    cfg: MemoryFactsSettings,
) -> FactStore:
    """按配置装配 FactStore 单例 (写路径 service 与读路径 memory_factory 共用).

    embedder 经 BoundModelResolver 解析 "semantic_history_embedding" 系统绑定
    (与语义历史召回同一套向量模型, 保证 query/document 向量空间一致).
    """
    from forge.memory.facts.store import FactStore
    from forge.memory.policies.conflict import ThresholdDedupResolver
    from forge.memory.policies.forgetting import NoForgetting

    async def _resolve_embedder() -> Any | None:
        from forge.retrieval.bound_model_resolver import get_bound_model_resolver

        return await get_bound_model_resolver().resolve("semantic_history_embedding")

    return FactStore(
        session_factory,
        conflict_resolver=ThresholdDedupResolver(cfg.dedup_threshold),
        forgetting_policy=NoForgetting(),
        embedder_resolver=_resolve_embedder,
        min_score=cfg.min_score,
        top_k_cap=max(cfg.top_k, 5),
    )


class FactExtractionService:
    """无状态. 复用单例即可."""

    async def extract_from_session(self, session_id: str, user_id: str) -> int:
        """抽取该 session 水位之后新消息中的用户长期事实.

        Returns:
            实际写入 (Insert/Replace/Merge) 的事实条数; 0 表示无新消息 /
            无可抽 / 全部去重, 均为业务正常.

        Raises:
            InfrastructureError: LLM 初始化 / DB 读写失败 (Celery 据此重试).
        """
        from forge.config.settings import get_settings
        from forge.infrastructure.database.database import get_session_factory, init_engine
        from forge.infrastructure.database.repositories.chat_message_repo import (
            ChatMessageRepository,
        )
        from forge.llm import LLMRequest, get_llm_gateway
        from forge.llm.gateway import StructuredOutputError
        from forge.llm.providers.base import ChatMessage
        from forge.memory.base import MemoryStoreError
        from forge.prompts import get_registry

        settings = get_settings()
        cfg = settings.memory.facts
        if not cfg.enabled:
            return 0

        init_engine()  # 幂等, worker 进程也安全
        factory = get_session_factory()

        # 1. 读水位 + 取增量消息
        try:
            watermark = await self._get_watermark(factory, session_id)
            async with factory() as db:
                rows = await ChatMessageRepository(db).load_after(
                    session_id,
                    watermark,
                    limit=settings.memory.summarizer.history_limit,
                )
        except SQLAlchemyError as exc:
            raise InfrastructureError(f"事实抽取读取消息失败: {exc}") from exc

        messages: list[Message] = []
        for row in rows:
            if row.role in ("user", "assistant") and row.content:
                role = cast(Literal["user", "assistant"], row.role)
                messages.append(Message(role=role, content=row.content))
        if len(messages) < 2:  # 连一轮完整对话都不到, 不值得送 LLM
            logger.info("事实抽取跳过 session=%s 水位后无足量新消息", session_id)
            return 0

        # 2. LLM 结构化抽取
        prompt = get_registry().render(
            PROMPT_NAME,
            messages=messages,
            max_facts=cfg.max_facts_per_turn,
        )
        try:
            gateway = get_llm_gateway(settings)
        except Exception as exc:
            logger.exception("事实抽取 LLM 初始化失败")
            raise InfrastructureError(f"事实抽取 LLM 初始化失败: {exc}") from exc

        req = LLMRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=0.2,
            max_tokens=1500,
            task_type="utility",
            model_profile="fast",
            preferred_provider=cfg.provider or None,
            preferred_model=cfg.model or None,
            cache_enabled=False,  # 抽取输入随对话变化, 精确缓存无意义
        )
        contents: list[str] = []
        try:
            resp = await gateway.complete_structured(
                req, schema=_EXTRACT_SCHEMA, name="extract_facts"
            )
            contents = [
                c.strip()
                for c in json.loads(resp.content or "{}").get("facts", [])
                if isinstance(c, str) and c.strip()
            ]
        except StructuredOutputError as exc:
            # 模型能力问题, 重试大概率仍失败: 跳过这一段, 照常推水位
            logger.warning("事实抽取结构化输出不合规, 跳过本段 session=%s: %s", session_id, exc)
        except Exception as exc:  # noqa: BLE001
            raise InfrastructureError(f"事实抽取 LLM 调用失败: {exc}") from exc

        # 3. 逐条写入 (经 ConflictResolver 去重); 单条失败不影响其余
        written = 0
        if contents:
            store = build_fact_store(factory, cfg)
            scope = MemoryScope.for_user(user_id)
            for content in contents[: cfg.max_facts_per_turn]:
                try:
                    fact = await store.write(
                        scope,
                        content=content,
                        source="llm_extracted",
                        source_session_id=session_id,
                    )
                except MemoryStoreError as exc:
                    logger.warning("事实写入失败 user=%s: %s", user_id, exc)
                    continue
                if fact is not None:
                    written += 1

        # 4. 推进水位 (无论 Insert/Skip/无可抽)
        await self._advance_watermark(factory, session_id, rows[-1].id)
        logger.info(
            "事实抽取完成 session=%s user=%s 抽取=%d 写入=%d 水位=%s",
            session_id,
            user_id,
            len(contents),
            written,
            rows[-1].id,
        )
        return written

    # ------------------------------------------------------------------
    # 水位读写 (select-then-write, 跨方言)
    # ------------------------------------------------------------------
    @staticmethod
    async def _get_watermark(
        factory: async_sessionmaker[AsyncSession], session_id: str
    ) -> str | None:
        from forge.infrastructure.database.orm.fact_extraction_watermark_orm import (
            FactExtractionWatermarkOrm,
        )

        sid = _to_int(session_id)
        if sid is None:
            return None
        async with factory() as db:
            row = (
                await db.execute(
                    select(FactExtractionWatermarkOrm).where(
                        FactExtractionWatermarkOrm.session_id == sid
                    )
                )
            ).scalar_one_or_none()
        return str(row.last_message_id) if row else None

    @staticmethod
    async def _advance_watermark(
        factory: async_sessionmaker[AsyncSession],
        session_id: str,
        last_message_id: str,
    ) -> None:
        from forge.infrastructure.database.orm.fact_extraction_watermark_orm import (
            FactExtractionWatermarkOrm,
        )

        sid = _to_int(session_id)
        mid = _to_int(last_message_id)
        if sid is None or mid is None:
            return
        try:
            async with factory() as db:
                row = (
                    await db.execute(
                        select(FactExtractionWatermarkOrm).where(
                            FactExtractionWatermarkOrm.session_id == sid
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    db.add(
                        FactExtractionWatermarkOrm(session_id=sid, last_message_id=mid)
                    )
                else:
                    # 并发兜底: 水位只前进不后退
                    row.last_message_id = max(row.last_message_id, mid)
                await db.commit()
        except IntegrityError:
            # 并发竞态 (两个 worker 同插同一 session): 水位幂等, 安静跳过
            logger.debug("水位推进命中并发插入, 跳过 session=%s", session_id)
        except SQLAlchemyError as exc:
            # 水位推进失败不算基础设施错: 下次抽取会重复处理这一段, 由去重兜底
            logger.warning("水位推进失败 session=%s: %s", session_id, exc)


def _to_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# 默认单例
_DEFAULT_SERVICE: FactExtractionService | None = None


def get_fact_extraction_service() -> FactExtractionService:
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = FactExtractionService()
    return _DEFAULT_SERVICE
