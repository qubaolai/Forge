"""SummaryStore: 会话摘要的持久化层.

设计:
    - 长寿单例: 构造时注入 async_sessionmaker, 每个方法内部自己 ``async with``
      开 session, 用完 commit 关闭. 不依赖外部事务边界 -- 摘要写入是独立后台
      任务, 跟 chat 请求的事务无关.
    - 失败 raise MemoryStoreError, 让 CompositeMemoryStore 决定是否吞掉
      (ContextBuilder 上游会降级).
    - 永远 upsert (session_id 主键), version 原地 +1.

无 FK 约束: session 删除时由 SessionLog-backed repository 显式调用
``delete(session_id)`` 清理摘要.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.infrastructure.database.orm.session_summary_orm import (
    SessionSummaryOrm,
)
from forge.memory.base import MemoryStoreError, Summary

logger = logging.getLogger(__name__)


def _to_int(value: str | int | None) -> int | None:
    """对外 ID (str(雪花)) → BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class SummaryStore:
    """会话摘要持久化. 长寿单例, 并发安全 (每方法自有 session)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._factory = session_factory

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    async def get(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> Summary | None:
        """取该 session 最新摘要; 没有返回 None.

        Raises:
            MemoryStoreError: DB 读取失败.
        """
        try:
            async with self._factory() as db:
                stmt = select(SessionSummaryOrm).where(SessionSummaryOrm.session_id == _to_int(session_id))
                if workspace_id is not None:
                    stmt = stmt.where(SessionSummaryOrm.workspace_id == workspace_id)
                row = (await db.execute(stmt)).scalar_one_or_none()
        except SQLAlchemyError as exc:
            logger.warning("SummaryStore.get 失败 session=%s: %s", session_id, exc)
            raise MemoryStoreError(f"SummaryStore.get failed: {exc}") from exc

        if row is None:
            return None
        return _orm_to_summary(row)

    # ------------------------------------------------------------------
    # 写: upsert (insert or update by session_id PK)
    # ------------------------------------------------------------------
    async def upsert(
        self,
        *,
        session_id: str,
        workspace_id: str | None = None,
        content: str,
        covered_until_message_id: str | None,
        token_count: int,
    ) -> Summary:
        """原地 upsert. 已存在则 version += 1, content 覆盖, updated_at 自动更新.

        Returns:
            写入后的 Summary (含最终 version).

        Raises:
            MemoryStoreError: DB 写入失败.
        """
        try:
            async with self._factory() as db:
                # MySQL ON DUPLICATE KEY UPDATE: 不存在 -> INSERT (version=1);
                # 存在 -> UPDATE 内容并 version+1. 一句完成原子 upsert.
                stmt = mysql_insert(SessionSummaryOrm).values(
                    session_id=_to_int(session_id),
                    workspace_id=workspace_id,
                    content=content,
                    covered_until_message_id=_to_int(covered_until_message_id),
                    token_count=token_count,
                    version=1,
                )
                stmt = stmt.on_duplicate_key_update(
                    content=stmt.inserted.content,
                    workspace_id=stmt.inserted.workspace_id,
                    covered_until_message_id=stmt.inserted.covered_until_message_id,
                    token_count=stmt.inserted.token_count,
                    version=SessionSummaryOrm.version + 1,
                )
                await db.execute(stmt)
                await db.commit()

                # 回查最终行 (拿到 server 端 version / updated_at)
                select_stmt = select(SessionSummaryOrm).where(
                    SessionSummaryOrm.session_id == _to_int(session_id)
                )
                if workspace_id is not None:
                    select_stmt = select_stmt.where(SessionSummaryOrm.workspace_id == workspace_id)
                row = (await db.execute(select_stmt)).scalar_one()
                return _orm_to_summary(row)
        except SQLAlchemyError as exc:
            logger.warning("SummaryStore.upsert 失败 session=%s: %s", session_id, exc)
            raise MemoryStoreError(f"SummaryStore.upsert failed: {exc}") from exc

    # ------------------------------------------------------------------
    # 删 (session 删除时清理; 由 SessionLog repo 显式调用, 无 FK cascade)
    # ------------------------------------------------------------------
    async def delete(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> None:
        try:
            async with self._factory() as db:
                stmt = delete(SessionSummaryOrm).where(SessionSummaryOrm.session_id == _to_int(session_id))
                if workspace_id is not None:
                    stmt = stmt.where(SessionSummaryOrm.workspace_id == workspace_id)
                await db.execute(stmt)
                await db.commit()
        except SQLAlchemyError as exc:
            logger.warning("SummaryStore.delete 失败 session=%s: %s", session_id, exc)
            raise MemoryStoreError(f"SummaryStore.delete failed: {exc}") from exc


def _orm_to_summary(row: SessionSummaryOrm) -> Summary:
    return Summary(
        session_id=str(row.session_id),
        content=row.content,
        covered_until_message_id=(
            str(row.covered_until_message_id)
            if row.covered_until_message_id is not None
            else None
        ),
        token_count=row.token_count,
        updated_at=row.updated_at or datetime.utcnow(),
        workspace_id=getattr(row, "workspace_id", None),
        version=row.version,
    )
