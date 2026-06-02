"""DigestStore: 消息 digest 缓存的持久化层 (context 子系统自有).

设计 (镜像 memory/summary/store.py 的长寿单例风格, 但归属 context, 不依赖 memory):
    - 注入 async_sessionmaker, 每方法内部自开 session, 用完 commit。
    - 按 message_id 原地 upsert (select-then-write, 跨方言可用)。
    - 读侧 batch_get 仅返回 status="done" 的记录, 供 DigestPolicy 命中无损 digest。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.context_mgmt.digest.types import DigestRecord, Segment
from forge.infrastructure.database.orm.message_digest_orm import MessageDigestOrm

logger = logging.getLogger(__name__)


def _to_int(value: str | int | None) -> int | None:
    """对外 ID (str(雪花)) → BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class DigestStore:
    """消息 digest 持久化. 长寿单例, 并发安全 (每方法自有 session).

    对外 API 以 str(雪花) 表示 message_id / session_id (与消息视图一致),
    内部按 BIGINT 列读写。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    async def get(self, message_id: str) -> DigestRecord | None:
        """取单条 done 状态的 digest; 无 / 未完成返回 None。"""
        records = await self.batch_get([message_id])
        return records.get(str(message_id))

    async def batch_get(self, message_ids: list[str]) -> dict[str, DigestRecord]:
        """批量取 done 状态的 digest. 失败时返回空 dict (软降级, 不抛)。"""
        ids = [i for i in (_to_int(m) for m in message_ids) if i is not None]
        if not ids:
            return {}
        try:
            async with self._factory() as db:
                stmt = select(MessageDigestOrm).where(
                    MessageDigestOrm.message_id.in_(ids),
                    MessageDigestOrm.status == "done",
                )
                rows = (await db.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:  # noqa: BLE001
            logger.warning("DigestStore.batch_get 失败: %s", exc)
            return {}
        return {str(row.message_id): _orm_to_record(row) for row in rows}

    async def get_meta(self, message_id: str) -> tuple[str, str] | None:
        """取 (source_hash, status), 供异步 task 判 stale / 幂等。无则 None。"""
        return (await self.batch_get_meta([message_id])).get(str(message_id))

    async def batch_get_meta(
        self, message_ids: list[str]
    ) -> dict[str, tuple[str, str]]:
        """批量取 {message_id: (source_hash, status)}, 一次 IN 查询。失败返回空 dict。"""
        ids = [i for i in (_to_int(m) for m in message_ids) if i is not None]
        if not ids:
            return {}
        try:
            async with self._factory() as db:
                stmt = select(
                    MessageDigestOrm.message_id,
                    MessageDigestOrm.source_hash,
                    MessageDigestOrm.status,
                ).where(MessageDigestOrm.message_id.in_(ids))
                rows = (await db.execute(stmt)).all()
        except SQLAlchemyError as exc:  # noqa: BLE001
            logger.warning("DigestStore.batch_get_meta 失败: %s", exc)
            return {}
        return {str(r[0]): (r[1] or "", r[2] or "") for r in rows}

    # ------------------------------------------------------------------
    # 写: 按 message_id upsert (select-then-write, 跨方言)
    # ------------------------------------------------------------------
    async def upsert(
        self,
        *,
        message_id: str,
        session_id: str | None,
        segments: list[Segment],
        total_tokens: int,
        source_hash: str,
        model: str | None = None,
        status: str = "done",
    ) -> None:
        seg_payload = [_segment_to_dict(s) for s in segments]
        mid = _to_int(message_id)
        sid = _to_int(session_id)
        try:
            async with self._factory() as db:
                existing = (
                    await db.execute(
                        select(MessageDigestOrm).where(
                            MessageDigestOrm.message_id == mid
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    db.add(
                        MessageDigestOrm(
                            message_id=mid,
                            session_id=sid,
                            segments=seg_payload,
                            total_tokens=total_tokens,
                            source_hash=source_hash,
                            model=model,
                            status=status,
                        )
                    )
                else:
                    existing.session_id = sid
                    existing.segments = seg_payload
                    existing.total_tokens = total_tokens
                    existing.source_hash = source_hash
                    existing.model = model
                    existing.status = status
                await db.commit()
        except IntegrityError:
            # 并发竞态 (Celery 多 worker 同时插入同一 message_id): digest 幂等,
            # 另一 writer 已写入, 安静跳过即可。
            logger.debug("DigestStore.upsert 命中并发插入, 跳过 message=%s", message_id)
        except SQLAlchemyError as exc:  # noqa: BLE001
            logger.warning("DigestStore.upsert 失败 message=%s: %s", message_id, exc)
            raise


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------
def _segment_to_dict(seg: Segment) -> dict:
    return {
        "kind": seg.kind,
        "start_line": seg.start_line,
        "end_line": seg.end_line,
        "anchor": seg.anchor,
        "digest_text": seg.digest_text,
    }


def _dict_to_segment(d: dict) -> Segment:
    return Segment(
        kind=d.get("kind", "prose"),
        start_line=int(d.get("start_line", 0)),
        end_line=int(d.get("end_line", 0)),
        digest_text=d.get("digest_text", ""),
        anchor=d.get("anchor"),
    )


def _orm_to_record(row: MessageDigestOrm) -> DigestRecord:
    segments = tuple(_dict_to_segment(d) for d in (row.segments or []))
    return DigestRecord(
        ref=f"msg:{row.message_id}",
        total_tokens=row.total_tokens or 0,
        segments=segments,
        generated_at=row.updated_at,
        model=row.model,
    )
