"""MessageEmbeddingStore: 消息向量缓存的持久化层 (context.recall 子系统自有).

镜像 DigestStore 风格: 注入 async_sessionmaker, 每方法自开 session;
按 message_id 原地 upsert (select-then-write, 跨方言)。

读侧 batch_get 只返回与给定 model 匹配的向量 (模型切换后旧向量视为未命中, 不混用)。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.infrastructure.database.orm.message_embedding_orm import (
    MessageEmbeddingOrm,
)

logger = logging.getLogger(__name__)


def _to_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class MessageEmbeddingStore:
    """消息向量持久化. 长寿单例, 并发安全 (每方法自有 session)。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    async def batch_get(
        self, message_ids: list[str], *, model: str
    ) -> dict[str, list[float]]:
        """批量取与 model 匹配的向量 {message_id: vector}. 失败/无返回空 dict (软降级)。"""
        ids = [i for i in (_to_int(m) for m in message_ids) if i is not None]
        if not ids:
            return {}
        try:
            async with self._factory() as db:
                stmt = select(
                    MessageEmbeddingOrm.message_id,
                    MessageEmbeddingOrm.vector,
                ).where(
                    MessageEmbeddingOrm.message_id.in_(ids),
                    MessageEmbeddingOrm.model == model,
                )
                rows = (await db.execute(stmt)).all()
        except SQLAlchemyError as exc:  # noqa: BLE001
            logger.warning("MessageEmbeddingStore.batch_get 失败: %s", exc)
            return {}
        return {str(r[0]): list(r[1] or []) for r in rows}

    async def batch_get_meta(
        self, message_ids: list[str]
    ) -> dict[str, tuple[str, str]]:
        """批量取 {message_id: (source_hash, model)}, 供冷路径幂等去重。失败返回空 dict。"""
        ids = [i for i in (_to_int(m) for m in message_ids) if i is not None]
        if not ids:
            return {}
        try:
            async with self._factory() as db:
                stmt = select(
                    MessageEmbeddingOrm.message_id,
                    MessageEmbeddingOrm.source_hash,
                    MessageEmbeddingOrm.model,
                ).where(MessageEmbeddingOrm.message_id.in_(ids))
                rows = (await db.execute(stmt)).all()
        except SQLAlchemyError as exc:  # noqa: BLE001
            logger.warning("MessageEmbeddingStore.batch_get_meta 失败: %s", exc)
            return {}
        return {str(r[0]): (r[1] or "", r[2] or "") for r in rows}

    # ------------------------------------------------------------------
    # 写: 按 message_id upsert
    # ------------------------------------------------------------------
    async def upsert(
        self,
        *,
        message_id: str,
        session_id: str | None,
        model: str,
        dim: int,
        vector: list[float],
        source_hash: str,
    ) -> None:
        mid = _to_int(message_id)
        sid = _to_int(session_id)
        try:
            async with self._factory() as db:
                existing = (
                    await db.execute(
                        select(MessageEmbeddingOrm).where(
                            MessageEmbeddingOrm.message_id == mid
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    db.add(
                        MessageEmbeddingOrm(
                            message_id=mid,
                            session_id=sid,
                            model=model,
                            dim=dim,
                            vector=list(vector),
                            source_hash=source_hash,
                        )
                    )
                else:
                    existing.session_id = sid
                    existing.model = model
                    existing.dim = dim
                    existing.vector = list(vector)
                    existing.source_hash = source_hash
                await db.commit()
        except IntegrityError:
            # 并发竞态 (多 worker 同插同一 message_id): 向量幂等, 安静跳过。
            logger.debug(
                "MessageEmbeddingStore.upsert 命中并发插入, 跳过 message=%s", message_id
            )
        except SQLAlchemyError as exc:  # noqa: BLE001
            logger.warning(
                "MessageEmbeddingStore.upsert 失败 message=%s: %s", message_id, exc
            )
