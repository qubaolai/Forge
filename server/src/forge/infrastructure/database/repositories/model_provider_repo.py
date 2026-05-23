"""Provider 仓储 — LLM 供应商配置查询。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.model_orm import ModelOrm
from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
from forge.infrastructure.database.orm.provider_key_orm import ProviderKeyOrm


class ProviderRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_enabled(self) -> list[ProviderOrm]:
        res = await self.db.execute(
            select(ProviderOrm)
            .where(ProviderOrm.is_enabled == 1)
            .order_by(ProviderOrm.priority.desc())
        )
        return list(res.scalars().all())

    async def get_by_name(self, name: str) -> ProviderOrm | None:
        res = await self.db.execute(
            select(ProviderOrm).where(ProviderOrm.name == name)
        )
        return res.scalar_one_or_none()

    async def get_by_provider_id(self, provider_id: str) -> ProviderOrm | None:
        res = await self.db.execute(
            select(ProviderOrm).where(ProviderOrm.provider_id == provider_id)
        )
        return res.scalar_one_or_none()

    async def list_enabled_keys(self, provider_db_id: int) -> list[ProviderKeyOrm]:
        res = await self.db.execute(
            select(ProviderKeyOrm)
            .where(ProviderKeyOrm.provider_id == provider_db_id)
            .where(ProviderKeyOrm.is_enabled == 1)
        )
        return list(res.scalars().all())

    async def list_all_enabled_keys(self) -> list[tuple[ProviderKeyOrm, ProviderOrm]]:
        """获取所有已启用供应商的已启用 Key（联表查询，用于启动预热）。"""
        stmt = (
            select(ProviderKeyOrm, ProviderOrm)
            .join(ProviderOrm, ProviderOrm.id == ProviderKeyOrm.provider_id)
            .where(ProviderKeyOrm.is_enabled == 1)
            .where(ProviderOrm.is_enabled == 1)
            .order_by(ProviderOrm.priority.desc())
        )
        res = await self.db.execute(stmt)
        return [(row[0], row[1]) for row in res.all()]

    async def list_enabled_with_models(self) -> list[tuple[ProviderOrm, list[ModelOrm]]]:
        """获取所有已启用供应商及其已启用模型（用于启动时全量加载）。"""
        providers = await self.list_enabled()
        result: list[tuple[ProviderOrm, list[ModelOrm]]] = []
        for provider in providers:
            stmt = (
                select(ModelOrm)
                .where(ModelOrm.provider_id == provider.id)
                .where(ModelOrm.is_enabled == True)  # noqa: E712
                .order_by(ModelOrm.priority.desc(), ModelOrm.name)
            )
            res = await self.db.execute(stmt)
            models = list(res.scalars().all())
            result.append((provider, models))
        return result
