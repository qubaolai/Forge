"""MessageEmbeddingStore: 消息向量缓存的持久化层 (context.recall 子系统自有).

镜像 DigestStore 风格: 注入 async_sessionmaker, 每方法自开 session;
按 message_id 原地 upsert (select-then-write, 跨方言)。

存储瘦身:
- 向量以 int8 对称量化字节存 (vector_i8, LargeBinary), 而非 JSON 文本存浮点, 约 18× 压缩。
  余弦相似度对正标量不变, 故无需回存 scale —— 读时直接用 int8 还原值算 cosine。
- 读侧 batch_get 只返回与给定 model 匹配的向量 (模型切换后旧向量视为未命中, 不混用)。
- prune_session 按 retain_turns 淘汰每个 session 较早的向量, 把总量收敛为有界。
"""

from __future__ import annotations

import logging
from array import array

from sqlalchemy import delete, select
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


def _quantize_int8(vec: list[float]) -> bytes:
    """对称量化为 int8 字节: scale = max(|v|)/127; q = round(v/scale), clip 到 [-127,127].

    余弦相似度对正标量 scale 不变, 故无需回存 scale。全零向量 (peak=0) 退化为全零字节。
    """
    if not vec:
        return b""
    peak = max(abs(x) for x in vec)
    if peak == 0.0:
        return bytes(len(vec))
    scale = peak / 127.0
    return array(
        "b", (max(-127, min(127, round(x / scale))) for x in vec)
    ).tobytes()


def _dequantize_int8(blob: bytes | None) -> list[float]:
    """int8 字节还原为 list[float] (按量化值原样, 不乘 scale —— cosine 不受影响)。"""
    if not blob:
        return []
    return [float(x) for x in array("b", blob)]


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
                    MessageEmbeddingOrm.vector_i8,
                ).where(
                    MessageEmbeddingOrm.message_id.in_(ids),
                    MessageEmbeddingOrm.model == model,
                )
                rows = (await db.execute(stmt)).all()
        except SQLAlchemyError as exc:  # noqa: BLE001
            logger.warning("MessageEmbeddingStore.batch_get 失败: %s", exc)
            return {}
        return {str(r[0]): _dequantize_int8(r[1]) for r in rows}

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
        blob = _quantize_int8(vector)
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
                            vector_i8=blob,
                            source_hash=source_hash,
                        )
                    )
                else:
                    existing.session_id = sid
                    existing.model = model
                    existing.dim = dim
                    existing.vector_i8 = blob
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

    # ------------------------------------------------------------------
    # 淘汰: 每个 session 只驻留最近 K 条 (turn 粒度下 ≈ 最近 K 轮)
    # ------------------------------------------------------------------
    async def prune_session(self, session_id: str | None, keep_recent_turns: int) -> None:
        """删除该 session 中超出最近 keep_recent_turns 条的向量 (按 message_id desc)。

        keep<=0 不淘汰。失败软降级 (warning 不抛, 不影响主流程)。
        """
        if keep_recent_turns <= 0:
            return
        sid = _to_int(session_id)
        if sid is None:
            return
        try:
            async with self._factory() as db:
                keep_stmt = (
                    select(MessageEmbeddingOrm.message_id)
                    .where(MessageEmbeddingOrm.session_id == sid)
                    .order_by(MessageEmbeddingOrm.message_id.desc())
                    .limit(keep_recent_turns)
                )
                keep_ids = [r[0] for r in (await db.execute(keep_stmt)).all()]
                if not keep_ids:
                    return
                del_stmt = delete(MessageEmbeddingOrm).where(
                    MessageEmbeddingOrm.session_id == sid,
                    MessageEmbeddingOrm.message_id.notin_(keep_ids),
                )
                await db.execute(del_stmt)
                await db.commit()
        except SQLAlchemyError as exc:  # noqa: BLE001
            logger.warning(
                "MessageEmbeddingStore.prune_session 失败 session=%s: %s",
                session_id,
                exc,
            )
