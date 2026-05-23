"""Provider 仓储 — LLM 供应商配置查询。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

    async def list_enabled_keys(self, provider_db_id: int) -> list[ProviderKeyOrm]:
        res = await self.db.execute(
            select(ProviderKeyOrm)
            .where(ProviderKeyOrm.provider_id == provider_db_id)
            .where(ProviderKeyOrm.is_enabled == 1)
        )
        return list(res.scalars().all())
