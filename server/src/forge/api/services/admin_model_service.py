"""AdminModelService — 管理端模型配置的编排层。

编排顺序：DB 写入 → Redis 缓存刷新 → LLMClientPool 同步 → EventBus 通知。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from forge.infrastructure.event_bus import get_event_bus
from forge.llm.model_config_cache import ModelConfigCache
from forge.llm.model_sync_service import ModelSyncService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 事件名
EVENT_MODEL_CONFIG_CHANGED = "model_config_changed"


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
        from forge.infrastructure.database.repositories.model_provider_repo import ProviderRepository
        from forge.infrastructure.database.repositories.model_repo import ModelRepository

        provider_repo = ProviderRepository(self.db)
        model_repo = ModelRepository(self.db)

        providers = await provider_repo.list_enabled()
        result = []
        for p in providers:
            models = await model_repo.list_by_provider(p.id, enabled_only=False)
            keys = await provider_repo.list_enabled_keys(p.id)
            result.append({
                "id": p.id,
                "provider_id": p.provider_id,
                "name": p.name,
                "impl": p.impl,
                "base_url": p.base_url,
                "is_enabled": bool(p.is_enabled),
                "priority": p.priority,
                "routing_config": p.routing_config,
                "key_count": len(keys),
                "model_count": len(models),
                "models": [
                    {
                        "model_id": m.model_id,
                        "name": m.name,
                        "display_name": m.display_name,
                        "model_type": m.model_type,
                        "is_enabled": m.is_enabled,
                        "is_default": m.is_default,
                        "priority": m.priority,
                        "cost_tier": m.cost_tier,
                    }
                    for m in models
                ],
            })
        return result

    async def toggle_provider(self, provider_name: str, enabled: bool) -> dict:
        """启用/禁用供应商 → 同步 pool + Redis + 通知前端。

        Returns:
            {"provider": name, "enabled": bool, "pool": {"added": N, "removed": N}}
        """
        from datetime import datetime

        from forge.infrastructure.database.repositories.model_provider_repo import ProviderRepository
        from forge.llm.client_pool import get_llm_pool

        provider_repo = ProviderRepository(self.db)
        provider = await provider_repo.get_by_name(provider_name)
        if not provider:
            raise ValueError(f"供应商不存在: {provider_name!r}")

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

        model.is_enabled = enabled
        await self.db.flush()
        await self.db.commit()

        # 查 provider name
        provider = await self.db.get(ProviderOrm, model.provider_id)
        provider_name = provider.name if provider else "unknown"

        cache = self._cache
        await cache.reload_all(self.db)

        # 事件通知
        await get_event_bus().publish(EVENT_MODEL_CONFIG_CHANGED, {
            "type": "model_toggled",
            "provider": provider_name,
            "model": model.name,
            "model_type": model.model_type,
            "enabled": enabled,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return {"model_id": model_id, "enabled": enabled}

    async def set_default_model(self, model_id: str) -> dict:
        """设为默认模型。"""
        from datetime import datetime

        from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
        from forge.infrastructure.database.repositories.model_repo import ModelRepository

        model_repo = ModelRepository(self.db)
        model = await model_repo.get_by_model_id(model_id)
        if not model:
            raise ValueError(f"模型不存在: {model_id!r}")
        ok = await model_repo.set_default(model_id)
        if not ok:
            raise ValueError(f"模型不存在: {model_id!r}")
        await self.db.commit()
        cache = self._cache
        await cache.reload_all(self.db)
        provider = await self.db.get(ProviderOrm, model.provider_id)
        await get_event_bus().publish(EVENT_MODEL_CONFIG_CHANGED, {
            "type": "model_default_changed",
            "provider": provider.name if provider else "unknown",
            "model": model.name,
            "model_type": model.model_type,
            "timestamp": datetime.utcnow().isoformat(),
        })
        return {"model_id": model_id, "is_default": True}

    # ------------------------------------------------------------------
    # 模型同步
    # ------------------------------------------------------------------

    async def sync_provider_models(self, provider_name: str) -> dict:
        """手动触发某供应商的模型同步 → DB + Redis + 通知前端。

        Returns:
            {"provider": str, "models_synced": int, "models_staled": int}
        """
        from datetime import datetime

        result = await ModelSyncService(self._cache).sync_provider(self.db, provider_name)

        # 事件通知
        await get_event_bus().publish(EVENT_MODEL_CONFIG_CHANGED, {
            "type": "models_synced",
            "provider": provider_name,
            "count": result.models_synced,
            "created": result.models_created,
            "timestamp": datetime.utcnow().isoformat(),
        })

        return result.to_dict()
