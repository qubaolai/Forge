"""ModelConfigCache — Redis 缓存的模型配置读写封装。

启动或配置变更时采用“全删后重建”策略，避免残留脏索引：
    1. 删除 ``forge:model:*`` 命名空间
    2. 从 DB 全量读取 provider/key/model
    3. 重建 providers/keys/models/by_type/enabled 索引
    4. 写入 ready 标记键
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING

from forge.core.crypto import resolve_key
from forge.infrastructure.cache.redis_client import RedisClient

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_NO_TTL = 0
_CACHE_PREFIX = "forge:model:"

_PFX_PROVIDERS = "forge:model:providers"
_PFX_KEYS = "forge:model:keys:{}"  # + provider_name
_PFX_LIST = "forge:model:list:{}"  # + provider_name
_PFX_BY_TYPE = "forge:model:by_type:{}"  # + model_type
_PFX_ENABLED = "forge:model:enabled:{}"  # + provider_name
_KEY_READY = "forge:model:ready:v1"


class ModelConfigCache:
    """Redis 缓存的模型配置读写封装。"""

    _global: "ModelConfigCache | None" = None
    _global_lock = threading.Lock()

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis
        self._ready = False

    @classmethod
    def get_global(cls, redis: RedisClient | None = None) -> "ModelConfigCache":
        with cls._global_lock:
            if cls._global is not None:
                return cls._global
            client = redis or RedisClient.from_settings()
            cls._global = cls(client)
            return cls._global

    @property
    def ready(self) -> bool:
        return self._ready and self._redis.available

    async def is_ready(self) -> bool:
        """真实就绪判定。

        进程重启后 ``_ready`` 会丢失，因此优先读 Redis 中的 ready 标记键。
        """
        if self.ready:
            return True
        if not await self._redis.ping():
            return False
        raw = await self._redis.get(_KEY_READY)
        if not raw:
            return False
        self._ready = True
        return True

    async def _clear_namespace(self) -> int:
        deleted = await self._redis.delete_prefix(_CACHE_PREFIX)
        logger.info("ModelConfigCache 清理完成: prefix=%s deleted=%d", _CACHE_PREFIX, deleted)
        return deleted

    async def reload_all(self, db: AsyncSession) -> None:
        """从 DB 全量加载到 Redis。"""
        from forge.infrastructure.database.repositories.model_provider_repo import (
            ProviderRepository,
        )
        from forge.infrastructure.database.repositories.model_repo import ModelRepository

        if not await self._redis.ping():
            self._ready = False
            logger.warning("ModelConfigCache 加载跳过: Redis 不可用")
            return

        provider_repo = ProviderRepository(db)
        model_repo = ModelRepository(db)
        providers = await provider_repo.list_enabled()

        await self._clear_namespace()

        providers_data: dict[str, str] = {}
        all_types: dict[str, dict[str, str]] = {}
        model_count = 0

        for provider in providers:
            providers_data[provider.name] = json.dumps(
                {
                    "name": provider.name,
                    "provider_id": provider.provider_id,
                    "db_id": provider.id,
                    "impl": provider.impl or provider.name,
                    "base_url": provider.base_url,
                    "is_enabled": provider.is_enabled,
                    "priority": provider.priority,
                    "routing_config": provider.routing_config,
                },
                ensure_ascii=False,
            )

            keys = await provider_repo.list_enabled_keys(provider.id)
            keys_data = []
            for key_row in keys:
                plain_key = resolve_key(key_row.key_ciphertext)
                if not plain_key:
                    logger.warning("Key 解析为空 fingerprint=%s, 跳过", key_row.key_fingerprint)
                    continue
                keys_data.append(
                    {
                        "api_key": plain_key,
                        "fingerprint": key_row.key_fingerprint,
                        "weight": key_row.weight,
                    }
                )
            await self._redis.set(
                _PFX_KEYS.format(provider.name),
                json.dumps(keys_data, ensure_ascii=False),
                ttl=_NO_TTL,
            )

            models = await model_repo.list_by_provider(provider.id, enabled_only=False)
            models_hash: dict[str, str] = {}
            enabled_hash: dict[str, str] = {}
            for model in models:
                model_json = json.dumps(
                    {
                        "model_id": model.model_id,
                        "name": model.name,
                        "display_name": model.display_name,
                        "model_type": model.model_type,
                        "context_window": model.context_window,
                        "max_output_tokens": model.max_output_tokens,
                        "supports_tools": model.supports_tools,
                        "supports_images": model.supports_images,
                        "supports_thinking": model.supports_thinking,
                        "thinking_type": model.thinking_type,
                        "thinking_options": model.thinking_options,
                        "thinking_default": model.thinking_default,
                        "extra_params": model.extra_params,
                        "cost_tier": model.cost_tier,
                        "is_enabled": model.is_enabled,
                        "is_default": model.is_default,
                        "priority": model.priority,
                        "is_stale": model.is_stale,
                    },
                    ensure_ascii=False,
                )
                models_hash[model.name] = model_json
                model_count += 1
                if model.is_enabled:
                    enabled_hash[model.name] = "1"
                    all_types.setdefault(model.model_type, {})[f"{provider.name}:{model.name}"] = "1"

            await self._redis.hset(_PFX_LIST.format(provider.name), models_hash, ttl=_NO_TTL)
            await self._redis.hset(_PFX_ENABLED.format(provider.name), enabled_hash, ttl=_NO_TTL)

        await self._redis.hset(_PFX_PROVIDERS, providers_data, ttl=_NO_TTL)
        for model_type, entries in all_types.items():
            await self._redis.hset(_PFX_BY_TYPE.format(model_type), entries, ttl=_NO_TTL)

        ready_payload = {
            "providers": len(providers),
            "models": model_count,
            "types": len(all_types),
        }
        await self._redis.set(_KEY_READY, json.dumps(ready_payload, ensure_ascii=False), ttl=_NO_TTL)

        self._ready = True
        logger.info(
            "ModelConfigCache 全量加载完成: providers=%d models=%d types=%d",
            len(providers),
            model_count,
            len(all_types),
        )

    async def reload_provider(self, db: AsyncSession, provider_name: str) -> None:
        """兼容旧调用：当前策略统一执行全量重建。"""
        logger.info("ModelConfigCache 刷新请求 provider=%s，采用全量重建策略", provider_name)
        await self.reload_all(db)

    async def get_providers_enabled(self) -> list[dict]:
        raw = await self._redis.hgetall(_PFX_PROVIDERS)
        if not raw:
            return []
        result: list[dict] = []
        for _, data_str in raw.items():
            data = json.loads(data_str)
            if data.get("is_enabled"):
                result.append(data)
        result.sort(key=lambda item: item.get("priority", 0), reverse=True)
        return result

    async def get_provider(self, name: str) -> dict | None:
        raw = await self._redis.hgetall(_PFX_PROVIDERS)
        if not raw:
            return None
        data_str = raw.get(name)
        return json.loads(data_str) if data_str else None

    async def get_keys(self, provider_name: str) -> list[dict]:
        raw = await self._redis.get(_PFX_KEYS.format(provider_name))
        if not raw:
            return []
        return json.loads(raw)

    async def get_models(self, provider_name: str, enabled_only: bool = True) -> list[dict]:
        raw = await self._redis.hgetall(_PFX_LIST.format(provider_name))
        if not raw:
            return []
        enabled_set = await self._redis.hgetall(_PFX_ENABLED.format(provider_name)) or {}
        result: list[dict] = []
        for name, data_str in raw.items():
            if enabled_only and name not in enabled_set:
                continue
            result.append(json.loads(data_str))
        result.sort(key=lambda item: item.get("priority", 0), reverse=True)
        return result

    async def is_model_enabled(self, provider_name: str, model_name: str) -> bool:
        enabled = await self._redis.hgetall(_PFX_ENABLED.format(provider_name))
        if not enabled:
            return False
        return model_name in enabled

    async def get_models_by_type(self, model_type: str) -> list[dict]:
        raw = await self._redis.hgetall(_PFX_BY_TYPE.format(model_type))
        if not raw:
            return []

        per_provider_models: dict[str, dict[str, str]] = {}
        result: list[dict] = []
        for compound_key in raw:
            if ":" not in compound_key:
                continue
            provider_name, model_name = compound_key.split(":", 1)
            if provider_name not in per_provider_models:
                per_provider_models[provider_name] = (
                    await self._redis.hgetall(_PFX_LIST.format(provider_name)) or {}
                )
            model_raw = per_provider_models[provider_name]
            data_str = model_raw.get(model_name)
            if not data_str:
                continue
            data = json.loads(data_str)
            if data.get("is_enabled"):
                data["provider"] = provider_name
                result.append(data)
        result.sort(key=lambda item: item.get("priority", 0), reverse=True)
        return result

    async def get_model_detail(self, provider_name: str, model_name: str) -> dict | None:
        raw = await self._redis.hgetall(_PFX_LIST.format(provider_name))
        if not raw:
            return None
        data_str = raw.get(model_name)
        return json.loads(data_str) if data_str else None

    async def get_default_model(self, provider_name: str, model_type: str = "text") -> dict | None:
        models = await self.get_models(provider_name, enabled_only=True)
        for model in models:
            if model.get("model_type") == model_type and model.get("is_default"):
                return model
        for model in models:
            if model.get("model_type") == model_type:
                return model
        return None
