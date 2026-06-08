"""Model 仓储 — 模型信息 CRUD + 同步标记。"""

import logging
from datetime import datetime

from sqlalchemy import and_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.model_orm import ModelOrm
from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm

logger = logging.getLogger(__name__)


def _to_int(value: str | int | None) -> int | None:
    """对外 ID (str(雪花)) → BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ModelRepository:
    """模型仓储。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---- 查询 ----

    async def list_by_provider(self, provider_id: int, enabled_only: bool = False) -> list[ModelOrm]:
        stmt = select(ModelOrm).where(ModelOrm.provider_id == provider_id)
        if enabled_only:
            stmt = stmt.where(ModelOrm.is_enabled == True)
        res = await self.db.execute(stmt)
        return list(res.scalars().all())

    async def list_enabled_by_type(self, model_type: str) -> list[ModelOrm]:
        """获取某类型下所有已启用的模型（跨供应商）。"""
        stmt = (
            select(ModelOrm)
            .where(and_(ModelOrm.model_type == model_type, ModelOrm.is_enabled == True))
        )
        res = await self.db.execute(stmt)
        return list(res.scalars().all())

    async def get_by_biz_key(self, provider_id: int, model_type: str, name: str) -> ModelOrm | None:
        """按业务主键 (provider_id + model_type + name) 查找。"""
        stmt = select(ModelOrm).where(
            and_(
                ModelOrm.provider_id == provider_id,
                ModelOrm.model_type == model_type,
                ModelOrm.name == name,
            )
        )
        res = await self.db.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_model_id(self, model_id: str) -> ModelOrm | None:
        """按雪花 ID (str(id) 或 int) 查找。"""
        mid = _to_int(model_id)
        if mid is None:
            return None
        return await self.db.get(ModelOrm, mid)

    async def get_by_id(self, model_db_id: int) -> ModelOrm | None:
        return await self.db.get(ModelOrm, model_db_id)

    async def is_model_enabled(self, provider_name: str, model_name: str) -> bool:
        """联表查询：校验某 provider:model 是否已启用。"""
        stmt = (
            select(ModelOrm)
            .join(ProviderOrm, ProviderOrm.id == ModelOrm.provider_id)
            .where(
                and_(
                    ProviderOrm.name == provider_name,
                    ModelOrm.name == model_name,
                    ModelOrm.is_enabled == True,  # noqa: E712
                    ProviderOrm.is_enabled == 1,
                )
            )
            .limit(1)
        )
        res = await self.db.execute(stmt)
        return res.scalar_one_or_none() is not None

    async def list_all_enabled_with_provider(self) -> list[tuple[ModelOrm, ProviderOrm]]:
        """获取所有已启用模型及其供应商信息（用于启动时全量加载到 Redis）。"""
        stmt = (
            select(ModelOrm, ProviderOrm)
            .join(ProviderOrm, ProviderOrm.id == ModelOrm.provider_id)
            .where(
                and_(
                    ModelOrm.is_enabled == True,
                    ProviderOrm.is_enabled == 1,
                )
            )
        )
        res = await self.db.execute(stmt)
        return [(row[0], row[1]) for row in res.all()]

    # ---- 写入 ----

    # 同步时允许更新的字段（仅模型本身信息，不改业务字段）
    _SYNC_UPDATABLE_FIELDS = frozenset({"display_name"})

    async def sync_upsert(
        self, provider_id: int, model_data: dict
    ) -> tuple[ModelOrm, bool]:
        """同步模型：按业务主键 (provider_id + model_type + name) upsert。"""
        model_type = model_data.get("model_type", "chat")
        model_name = model_data["name"]
        existing = await self.get_by_biz_key(provider_id, model_type, model_name)
        created = existing is None

        bind = self.db.get_bind()
        dialect_name = bind.dialect.name if bind is not None else ""
        now = datetime.utcnow()

        if dialect_name.startswith("mysql"):
            insert_values = {
                "provider_id": provider_id,
                "name": model_name,
                "display_name": model_data.get("display_name", ""),
                "model_type": model_type,
                "cost_tier": model_data.get("cost_tier", "mid"),
                "is_enabled": True,
                "last_synced_at": now,
                "is_stale": False,
            }
            stmt = mysql_insert(ModelOrm).values(**insert_values)
            update_values = {
                field: insert_values[field]
                for field in self._SYNC_UPDATABLE_FIELDS
                if field in insert_values
            }
            update_values["last_synced_at"] = now
            update_values["is_stale"] = False
            stmt = stmt.on_duplicate_key_update(**update_values)
            await self.db.execute(stmt)
            await self.db.flush()
            model = await self.get_by_biz_key(provider_id, model_type, model_name)
            if model is None:
                raise RuntimeError(f"ON DUPLICATE KEY UPDATE 后未找到模型: {provider_id}:{model_type}:{model_name}")
            return model, created

        # 非 MySQL 兼容分支：保持原有行为（开发环境常见 SQLite）
        if existing:
            for field in self._SYNC_UPDATABLE_FIELDS:
                if field in model_data:
                    setattr(existing, field, model_data[field])
            await self.db.flush()
            return existing, False

        model = ModelOrm(
            provider_id=provider_id,
            name=model_name,
            display_name=model_data.get("display_name", ""),
            model_type=model_type,
            cost_tier=model_data.get("cost_tier", "mid"),
            is_enabled=True,
            last_synced_at=now,
            is_stale=False,
        )
        self.db.add(model)
        await self.db.flush()
        return model, True

    async def mark_stale(
        self, provider_id: int, active_names: set[str], model_type: str = "chat"
    ) -> int:
        """将不在 active_names 中的同类型模型标记为 is_stale=True。返回标记数量。"""
        stmt = (
            update(ModelOrm)
            .where(
                and_(
                    ModelOrm.provider_id == provider_id,
                    ModelOrm.model_type == model_type,
                    ModelOrm.name.notin_(active_names),
                )
            )
            .values(is_stale=True)
        )
        res = await self.db.execute(stmt)
        await self.db.flush()
        return res.rowcount  # type: ignore[attr-defined]

    async def set_enabled(self, model_id: str, enabled: bool) -> bool:
        """切换模型启用状态。返回是否成功。"""
        model = await self.get_by_model_id(model_id)
        if not model:
            return False
        model.is_enabled = enabled
        await self.db.flush()
        return True

    # ---- 管理端手动 CRUD ----

    # 管理端允许更新的字段（不含 provider_id / model_id 等业务主键）
    _ADMIN_UPDATABLE_FIELDS = frozenset({
        "display_name", "is_enabled",
    })

    _NULLABLE_ADMIN_FIELDS = frozenset()

    async def create(self, provider_id: int, data: dict) -> ModelOrm:
        """管理端手动新增模型。"""
        model = ModelOrm(
            provider_id=provider_id,
            name=data["name"],
            display_name=data.get("display_name", ""),
            model_type=data.get("model_type", "chat"),
            is_enabled=data.get("is_enabled", True),
        )
        self.db.add(model)
        await self.db.flush()
        return model

    async def update_fields(self, model_id: str, data: dict) -> bool:
        """管理端更新模型字段（仅 _ADMIN_UPDATABLE_FIELDS 内）。返回是否成功。"""
        model = await self.get_by_model_id(model_id)
        if not model:
            return False
        for field, value in data.items():
            if field not in self._ADMIN_UPDATABLE_FIELDS:
                continue
            if value is None and field not in self._NULLABLE_ADMIN_FIELDS:
                continue
            setattr(model, field, value)
        await self.db.flush()
        return True

    async def delete(self, model_id: str) -> bool:
        """管理端删除模型。返回是否成功。"""
        model = await self.get_by_model_id(model_id)
        if not model:
            return False
        await self.db.delete(model)
        await self.db.flush()
        return True
