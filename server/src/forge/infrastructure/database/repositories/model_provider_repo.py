"""Provider 仓储 — LLM 供应商配置查询。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.model_orm import ModelOrm
from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
from forge.infrastructure.database.orm.provider_key_orm import ProviderKeyOrm


def _to_int(value: str | int | None) -> int | None:
    """对外 ID (str(雪花)) → BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ProviderRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_enabled(self) -> list[ProviderOrm]:
        res = await self.db.execute(
            select(ProviderOrm)
            .where(ProviderOrm.is_enabled == 1)
        )
        return list(res.scalars().all())

    async def list_all(self) -> list[ProviderOrm]:
        res = await self.db.execute(
            select(ProviderOrm)
            .order_by(ProviderOrm.id.asc())
        )
        return list(res.scalars().all())

    async def get_by_id(self, provider_db_id: int) -> ProviderOrm | None:
        return await self.db.get(ProviderOrm, provider_db_id)

    async def get_by_name(self, name: str) -> ProviderOrm | None:
        res = await self.db.execute(
            select(ProviderOrm).where(ProviderOrm.name == name)
        )
        return res.scalar_one_or_none()

    async def get_by_provider_id(self, provider_id: str) -> ProviderOrm | None:
        """按雪花 ID (str(id) 或 int) 查找。"""
        pid = _to_int(provider_id)
        if pid is None:
            return None
        return await self.db.get(ProviderOrm, pid)

    async def list_enabled_keys(self, provider_db_id: int) -> list[ProviderKeyOrm]:
        res = await self.db.execute(
            select(ProviderKeyOrm)
            .where(ProviderKeyOrm.provider_id == provider_db_id)
            .where(ProviderKeyOrm.is_enabled == 1)
        )
        return list(res.scalars().all())

    # ---- 管理端 API-Key CRUD ----

    async def list_keys(self, provider_db_id: int) -> list[ProviderKeyOrm]:
        """列出某供应商全部 Key（含已禁用）。"""
        res = await self.db.execute(
            select(ProviderKeyOrm)
            .where(ProviderKeyOrm.provider_id == provider_db_id)
            .order_by(ProviderKeyOrm.id.asc())
        )
        return list(res.scalars().all())

    async def get_key(self, key_id: str) -> ProviderKeyOrm | None:
        """按雪花 ID (str(id) 或 int) 查找。"""
        kid = _to_int(key_id)
        if kid is None:
            return None
        return await self.db.get(ProviderKeyOrm, kid)

    async def create_key(
        self, provider_db_id: int, *, ciphertext: str, fingerprint: str, weight: int = 1
    ) -> ProviderKeyOrm:
        """新增一条 API-Key（密文 + 脱敏指纹）。"""
        key = ProviderKeyOrm(
            provider_id=provider_db_id,
            key_ciphertext=ciphertext,
            key_fingerprint=fingerprint,
            is_enabled=1,
            weight=weight,
        )
        self.db.add(key)
        await self.db.flush()
        return key

    async def update_key(
        self, key_id: str, *, enabled: bool | None = None, weight: int | None = None
    ) -> ProviderKeyOrm | None:
        """更新 Key 的启停 / 权重。返回更新后的 ORM（不存在则 None）。"""
        key = await self.get_key(key_id)
        if not key:
            return None
        if enabled is not None:
            key.is_enabled = 1 if enabled else 0
        if weight is not None:
            key.weight = weight
        await self.db.flush()
        return key

    async def delete_key(self, key_id: str) -> bool:
        """删除一条 API-Key。返回是否成功。"""
        key = await self.get_key(key_id)
        if not key:
            return False
        await self.db.delete(key)
        await self.db.flush()
        return True

    async def list_all_enabled_keys(self) -> list[tuple[ProviderKeyOrm, ProviderOrm]]:
        """获取所有已启用供应商的已启用 Key（联表查询，用于启动预热）。"""
        stmt = (
            select(ProviderKeyOrm, ProviderOrm)
            .join(ProviderOrm, ProviderOrm.id == ProviderKeyOrm.provider_id)
            .where(ProviderKeyOrm.is_enabled == 1)
            .where(ProviderOrm.is_enabled == 1)
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
                .where(ModelOrm.is_enabled == True)
            )
            res = await self.db.execute(stmt)
            models = list(res.scalars().all())
            result.append((provider, models))
        return result
