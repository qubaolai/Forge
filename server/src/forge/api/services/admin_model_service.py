"""AdminModelService — 管理端模型配置的编排层。

编排顺序：DB 写入 → Redis 缓存刷新 → LLMClientPool 同步 → EventBus 通知。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from forge.infrastructure.event_bus import get_event_bus
from forge.llm.model_config_cache import ModelConfigCache

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 事件名
EVENT_MODEL_CONFIG_CHANGED = "model_config_changed"


def _key_fingerprint(api_key: str) -> str:
    """生成脱敏指纹（仅展示/日志用，沿用 api_key 前缀约定，截到 12 字符内）。"""
    head = (api_key or "")[:6]
    return f"{head}***"[:12]


async def _model_to_dict(db, model) -> dict:
    from forge.infrastructure.database.repositories.model_config_repo import ModelConfigRepository

    config_repo = ModelConfigRepository(db)
    config = config_repo.to_dict(await config_repo.get(model.id, model.model_type))
    return {
        "id": str(model.id),
        "model_id": str(model.id),
        "provider_id": str(model.provider_id),
        "name": model.name,
        "display_name": model.display_name,
        "model_type": model.model_type,
        "config": config,
        "is_enabled": model.is_enabled
    }


def _key_to_dict(key) -> dict:
    """脱敏 Key 视图（绝不含明文/密文）。"""
    return {
        "key_id": str(key.id),
        "key_fingerprint": key.key_fingerprint,
        "is_enabled": bool(key.is_enabled),
        "weight": key.weight,
        "cooldown_until": key.cooldown_until.isoformat() if key.cooldown_until else None,
        "failure_score": key.failure_score,
        "last_error_at": key.last_error_at.isoformat() if key.last_error_at else None,
    }


class AdminModelService:
    """管理端模型配置编排。"""

    def __init__(self, db: AsyncSession, model_cache: ModelConfigCache | None = None) -> None:
        self.db = db
        self._cache = model_cache or ModelConfigCache.get_global()

    # ------------------------------------------------------------------
    # 供应商管理
    # ------------------------------------------------------------------

    async def list_providers(self) -> list[dict]:
        """列出所有供应商及其模型。"""
        from forge.infrastructure.database.repositories.model_provider_repo import (
            ProviderRepository,
        )
        from forge.infrastructure.database.repositories.model_repo import ModelRepository

        provider_repo = ProviderRepository(self.db)
        model_repo = ModelRepository(self.db)

        providers = await provider_repo.list_all()
        result = []
        for p in providers:
            models = await model_repo.list_by_provider(p.id, enabled_only=False)
            keys = await provider_repo.list_keys(p.id)
            result.append({
                "id": str(p.id),
                "provider_id": str(p.id),
                "name": p.name,
                "impl": p.impl,
                "base_url": p.base_url,
                "is_enabled": bool(p.is_enabled),
                "routing_config": p.routing_config,
                "key_count": len(keys),
                "model_count": len(models),
                "models": [await _model_to_dict(self.db, m) for m in models],
            })
        return result

    async def toggle_provider(self, provider_name: str, enabled: bool) -> dict:
        """启用/禁用供应商 → 同步 pool + Redis + 通知前端。

        Returns:
            {"provider": name, "enabled": bool, "pool": {"added": N, "removed": N}}
        """
        from datetime import datetime

        from forge.infrastructure.database.repositories.model_provider_repo import (
            ProviderRepository,
        )
        from forge.llm.client_pool import get_llm_pool

        provider_repo = ProviderRepository(self.db)
        provider = await provider_repo.get_by_name(provider_name)
        if not provider:
            raise ValueError(f"供应商不存在: {provider_name!r}")
        if not enabled:
            await self._ensure_provider_not_bound(provider.id)

        provider.is_enabled = 1 if enabled else 0
        await self.db.flush()
        await self.db.commit()

        cache = self._cache
        pool_result = {"added": 0, "removed": 0}
        pool = get_llm_pool()
        impl = provider.impl or provider_name
        client_options = {"base_url": provider.base_url, "timeout": 30}

        if enabled:
            await cache.reload_all(self.db)
            keys = await cache.get_keys(provider_name)
            pool_result = pool.reconcile_provider(impl, keys, client_options)
        else:
            await cache.reload_all(self.db)
            pool_result = pool.reconcile_provider(impl, [], client_options)

        # 事件通知
        from forge.retrieval.bound_model_resolver import get_bound_model_resolver

        get_bound_model_resolver().clear()
        await get_event_bus().publish(EVENT_MODEL_CONFIG_CHANGED, {
            "type": "provider_toggled",
            "provider": provider_name,
            "enabled": enabled,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return {
            "provider": provider_name,
            "enabled": enabled,
            "pool": pool_result,
        }

    # ------------------------------------------------------------------
    # 模型管理
    # ------------------------------------------------------------------

    async def toggle_model(self, model_id: str, enabled: bool) -> dict:
        """切换模型启用状态 → 刷新 Redis + 通知前端。

        Returns:
            {"model_id": str, "enabled": bool}
        """
        from datetime import datetime

        from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
        from forge.infrastructure.database.repositories.model_repo import ModelRepository

        model_repo = ModelRepository(self.db)

        model = await model_repo.get_by_model_id(model_id)
        if not model:
            raise ValueError(f"模型不存在: {model_id!r}")
        if not enabled:
            await self._ensure_model_not_bound(model.id, action="禁用")

        model.is_enabled = enabled
        await self.db.flush()
        await self.db.commit()

        # 查 provider name
        provider = await self.db.get(ProviderOrm, model.provider_id)
        provider_name = provider.name if provider else "unknown"

        cache = self._cache
        await cache.reload_all(self.db)

        # 事件通知
        from forge.retrieval.bound_model_resolver import get_bound_model_resolver

        get_bound_model_resolver().clear()
        await get_event_bus().publish(EVENT_MODEL_CONFIG_CHANGED, {
            "type": "model_toggled",
            "provider": provider_name,
            "model": model.name,
            "model_type": model.model_type,
            "enabled": enabled,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return {"model_id": model_id, "enabled": enabled}

    async def get_model_by_id(self, model_db_id: int) -> dict:
        """按数据库 id 获取模型详情。"""
        from forge.infrastructure.database.repositories.model_repo import ModelRepository

        model = await ModelRepository(self.db).get_by_id(model_db_id)
        if not model:
            raise ValueError(f"模型不存在: id={model_db_id}")
        return await _model_to_dict(self.db, model)

    # ------------------------------------------------------------------
    # 模型手动 CRUD
    # ------------------------------------------------------------------

    async def create_model(self, provider_name: str, data: dict) -> dict:
        """管理端在某供应商下手动新增模型。"""
        from forge.infrastructure.database.repositories.model_config_repo import (
            ModelConfigRepository,
        )
        from forge.infrastructure.database.repositories.model_provider_repo import (
            ProviderRepository,
        )
        from forge.infrastructure.database.repositories.model_repo import ModelRepository

        provider = await ProviderRepository(self.db).get_by_name(provider_name)
        if not provider:
            raise ValueError(f"供应商不存在: {provider_name!r}")
        model = await ModelRepository(self.db).create(provider.id, data)
        await ModelConfigRepository(self.db).create(model.id, model.model_type, data["config"])
        await self.db.commit()
        await self._cache.reload_all(self.db)
        await self._publish_model_event("model_created", provider.name, model.name, model.model_type)
        return await _model_to_dict(self.db, model)

    async def update_model_fields(self, model_id: str, data: dict) -> dict:
        """管理端更新模型与类型配置。"""
        from forge.api.schemas.admin import validate_model_config
        from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
        from forge.infrastructure.database.repositories.model_config_repo import (
            ModelConfigRepository,
        )
        from forge.infrastructure.database.repositories.model_repo import ModelRepository

        model_repo = ModelRepository(self.db)
        model = await model_repo.get_by_model_id(model_id)
        if not model:
            raise ValueError(f"模型不存在: {model_id!r}")

        config_data = data.pop("config", None)
        fields = dict(data)
        if "enabled" in fields:
            fields["is_enabled"] = fields.pop("enabled")
        if fields.get("is_enabled") is False:
            await self._ensure_model_not_bound(model.id, action="禁用")
        if fields:
            await model_repo.update_fields(model_id, fields)
        if config_data is not None:
            validated = validate_model_config(model.model_type, config_data)
            config_repo = ModelConfigRepository(self.db)
            existing = await config_repo.get(model.id, model.model_type)
            if (
                model.model_type == "embedding"
                and existing is not None
                and int(validated["dimension"]) != int(existing.dimension)
            ):
                raise ValueError("Embedding dimension 不可编辑，请新增模型后切换绑定")
            await config_repo.update(model.id, model.model_type, validated)

        await self.db.commit()
        await self._cache.reload_all(self.db)
        provider = await self.db.get(ProviderOrm, model.provider_id)
        await self._publish_model_event(
            "model_updated", provider.name if provider else "unknown", model.name, model.model_type
        )
        return await _model_to_dict(self.db, model)

    async def delete_model(self, model_id: str) -> dict:
        """管理端删除模型。"""
        from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
        from forge.infrastructure.database.repositories.model_repo import ModelRepository

        model_repo = ModelRepository(self.db)
        model = await model_repo.get_by_model_id(model_id)
        if not model:
            raise ValueError(f"模型不存在: {model_id!r}")
        await self._ensure_model_not_bound(model.id, action="删除")
        provider = await self.db.get(ProviderOrm, model.provider_id)
        provider_name = provider.name if provider else "unknown"
        model_name, model_type = model.name, model.model_type

        from forge.infrastructure.database.repositories.model_config_repo import (
            ModelConfigRepository,
        )
        config = await ModelConfigRepository(self.db).get(model.id, model.model_type)
        if config is not None:
            await self.db.delete(config)
        await model_repo.delete(model_id)
        await self.db.commit()
        await self._cache.reload_all(self.db)
        await self._publish_model_event("model_deleted", provider_name, model_name, model_type)
        return {"model_id": model_id, "deleted": True}

    # ------------------------------------------------------------------
    # 供应商 API-Key CRUD
    # ------------------------------------------------------------------

    async def list_provider_keys(self, provider_name: str) -> list[dict]:
        from forge.infrastructure.database.repositories.model_provider_repo import (
            ProviderRepository,
        )

        provider_repo = ProviderRepository(self.db)
        provider = await provider_repo.get_by_name(provider_name)
        if not provider:
            raise ValueError(f"供应商不存在: {provider_name!r}")
        keys = await provider_repo.list_keys(provider.id)
        return [_key_to_dict(k) for k in keys]

    async def create_provider_key(self, provider_name: str, api_key: str, weight: int = 1) -> dict:
        from forge.core.crypto import encrypt
        from forge.infrastructure.database.repositories.model_provider_repo import (
            ProviderRepository,
        )

        provider_repo = ProviderRepository(self.db)
        provider = await provider_repo.get_by_name(provider_name)
        if not provider:
            raise ValueError(f"供应商不存在: {provider_name!r}")

        key = await provider_repo.create_key(
            provider.id,
            ciphertext=encrypt(api_key),
            fingerprint=_key_fingerprint(api_key),
            weight=weight,
        )
        await self.db.commit()
        await self._sync_provider_pool(provider)
        await self._publish_key_event("provider_key_created", provider.name)
        return _key_to_dict(key)

    async def update_provider_key(
        self, provider_name: str, key_id: str, *, enabled: bool | None, weight: int | None
    ) -> dict:
        from forge.infrastructure.database.repositories.model_provider_repo import (
            ProviderRepository,
        )

        provider_repo = ProviderRepository(self.db)
        provider = await provider_repo.get_by_name(provider_name)
        if not provider:
            raise ValueError(f"供应商不存在: {provider_name!r}")
        existing = await provider_repo.get_key(key_id)
        if not existing or existing.provider_id != provider.id:
            raise ValueError(f"Key 不存在: {key_id!r}")

        key = await provider_repo.update_key(key_id, enabled=enabled, weight=weight)
        await self.db.commit()
        await self._sync_provider_pool(provider)
        await self._publish_key_event("provider_key_updated", provider.name)
        return _key_to_dict(key)

    async def delete_provider_key(self, provider_name: str, key_id: str) -> dict:
        from forge.infrastructure.database.repositories.model_provider_repo import (
            ProviderRepository,
        )

        provider_repo = ProviderRepository(self.db)
        provider = await provider_repo.get_by_name(provider_name)
        if not provider:
            raise ValueError(f"供应商不存在: {provider_name!r}")
        existing = await provider_repo.get_key(key_id)
        if not existing or existing.provider_id != provider.id:
            raise ValueError(f"Key 不存在: {key_id!r}")

        await provider_repo.delete_key(key_id)
        await self.db.commit()
        await self._sync_provider_pool(provider)
        await self._publish_key_event("provider_key_deleted", provider.name)
        return {"key_id": key_id, "deleted": True}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    async def _ensure_model_not_bound(self, model_id: int, *, action: str) -> None:
        from sqlalchemy import select

        from forge.infrastructure.database.orm.system_model_binding_orm import (
            SystemModelBindingOrm,
        )

        bound = (await self.db.execute(
            select(SystemModelBindingOrm).where(SystemModelBindingOrm.model_id == model_id)
        )).scalars().first()
        if bound is not None:
            raise ValueError(f"模型正在被系统角色 {bound.role} 使用，不能{action}")

    async def _ensure_provider_not_bound(self, provider_id: int) -> None:
        from sqlalchemy import select

        from forge.infrastructure.database.orm.model_orm import ModelOrm
        from forge.infrastructure.database.orm.system_model_binding_orm import (
            SystemModelBindingOrm,
        )

        bound = (await self.db.execute(
            select(SystemModelBindingOrm)
            .join(ModelOrm, ModelOrm.id == SystemModelBindingOrm.model_id)
            .where(ModelOrm.provider_id == provider_id)
        )).scalars().first()
        if bound is not None:
            raise ValueError(f"供应商存在系统角色 {bound.role} 使用的模型，不能禁用")

    async def _sync_provider_pool(self, provider) -> dict:
        """刷新 Redis 缓存 + 按当前已启用 Key 重整 LLMClientPool。"""
        from forge.llm.client_pool import get_llm_pool

        await self._cache.reload_all(self.db)
        pool = get_llm_pool()
        impl = provider.impl or provider.name
        client_options = {"base_url": provider.base_url, "timeout": 30}
        keys = await self._cache.get_keys(provider.name)
        return pool.reconcile_provider(impl, keys, client_options)

    async def _publish_model_event(
        self, event_type: str, provider_name: str, model_name: str, model_type: str
    ) -> None:
        from datetime import datetime

        from forge.retrieval.bound_model_resolver import get_bound_model_resolver

        get_bound_model_resolver().clear()
        await get_event_bus().publish(EVENT_MODEL_CONFIG_CHANGED, {
            "type": event_type,
            "provider": provider_name,
            "model": model_name,
            "model_type": model_type,
            "timestamp": datetime.utcnow().isoformat(),
        })

    async def _publish_key_event(self, event_type: str, provider_name: str) -> None:
        from datetime import datetime

        from forge.retrieval.bound_model_resolver import get_bound_model_resolver

        get_bound_model_resolver().clear()
        await get_event_bus().publish(EVENT_MODEL_CONFIG_CHANGED, {
            "type": event_type,
            "provider": provider_name,
            "timestamp": datetime.utcnow().isoformat(),
        })
