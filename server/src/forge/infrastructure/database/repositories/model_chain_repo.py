"""ModelChain 仓储 — 对话链 / 档位链 CRUD。

entries 统一为有序 list[dict]:[{"provider": str, "model": str}, ...]。
不做存在性/启用校验(那是 service 层 + 缓存的职责),仓储只管落库读取。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.model_chain_orm import ModelChainOrm

logger = logging.getLogger(__name__)

SCOPE_CONVERSATION = "conversation"
SCOPE_TIER = "tier"
TIER_KEYS = ("fast", "smart", "strong")


def _to_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ModelChainRepository:
    """模型调用链仓储。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, scope: str, chain_key: str) -> ModelChainOrm | None:
        stmt = select(ModelChainOrm).where(
            ModelChainOrm.scope == scope,
            ModelChainOrm.chain_key == chain_key,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_by_scope(self, scope: str) -> list[ModelChainOrm]:
        stmt = (
            select(ModelChainOrm)
            .where(ModelChainOrm.scope == scope)
            .order_by(ModelChainOrm.chain_key)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_all(self) -> list[ModelChainOrm]:
        stmt = select(ModelChainOrm).order_by(ModelChainOrm.scope, ModelChainOrm.chain_key)
        return list((await self.db.execute(stmt)).scalars().all())

    async def upsert(
        self,
        scope: str,
        chain_key: str,
        entries: list[dict],
        *,
        updated_by: str | None = None,
    ) -> ModelChainOrm:
        """整组有序覆盖 entries,version 自增。"""
        row = await self.get(scope, chain_key)
        if row is None:
            row = ModelChainOrm(
                scope=scope,
                chain_key=chain_key,
                entries=entries,
                version=1,
                updated_by=_to_int(updated_by),
            )
            self.db.add(row)
        else:
            row.entries = entries
            row.version = int(row.version or 0) + 1
            row.updated_by = _to_int(updated_by)
        await self.db.flush()
        return row
