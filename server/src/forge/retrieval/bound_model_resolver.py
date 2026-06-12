"""按系统绑定解析 Embedding/Reranker 运行时实例。"""

from __future__ import annotations

import threading
from typing import Any, cast

from sqlalchemy import select

from forge.core.crypto import resolve_key
from forge.infrastructure.database.database import session_scope
from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
from forge.infrastructure.database.orm.system_model_binding_orm import SystemModelBindingOrm
from forge.infrastructure.database.repositories.model_config_repo import ModelConfigRepository
from forge.infrastructure.database.repositories.model_provider_repo import ProviderRepository
from forge.infrastructure.database.repositories.model_repo import ModelRepository


class BoundModelResolver:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, int], object] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    async def resolve(self, role: str):
        async with session_scope() as db:
            binding = (await db.execute(
                select(SystemModelBindingOrm).where(SystemModelBindingOrm.role == role)
            )).scalar_one_or_none()
            if binding is None or binding.model_id is None:
                return None
            cache_key = (role, int(binding.version or 0))
            with self._lock:
                if cache_key in self._cache:
                    return self._cache[cache_key]

            model = await ModelRepository(db).get_by_id(binding.model_id)
            if model is None or not model.is_enabled:
                raise RuntimeError(f"系统绑定 {role} 指向的模型不可用")
            provider = await db.get(ProviderOrm, model.provider_id)
            if provider is None or not provider.is_enabled:
                raise RuntimeError(f"系统绑定 {role} 指向的供应商不可用")
            config_row = await ModelConfigRepository(db).get(model.id, model.model_type)
            if config_row is None:
                raise RuntimeError(f"模型 {model.name} 缺少 {model.model_type} 配置")
            config = ModelConfigRepository.to_dict(config_row)
            keys = await ProviderRepository(db).list_enabled_keys(provider.id)

        impl = provider.impl or provider.name
        api_key = None
        for key in keys:
            resolved = resolve_key(key.key_ciphertext)
            if resolved:
                api_key = resolved
                break
        if impl != "mock" and not api_key:
            raise RuntimeError(f"供应商 {provider.name} 没有可用 API-Key")
        runtime_config = self._runtime_config(model.name, model.model_type, config, api_key)
        instance: object
        if model.model_type == "embedding":
            from forge.retrieval.embedders.factory import EmbedderFactory
            instance = EmbedderFactory.create(impl, runtime_config)
        elif model.model_type == "reranker":
            from forge.retrieval.rerankers.factory import RerankerFactory
            instance = RerankerFactory.create(impl, runtime_config)
        else:
            raise RuntimeError(f"系统绑定不支持模型类型: {model.model_type}")
        cast(Any, instance)._forge_model_id = str(model.id)
        with self._lock:
            self._cache = {
                key: value
                for key, value in self._cache.items()
                if key[0] != role
            }
            self._cache[cache_key] = instance
        return instance

    @staticmethod
    def _runtime_config(name: str, model_type: str, config: dict, api_key: str | None) -> dict:
        result = dict(config.get("provider_options") or {})
        result["model"] = name
        if api_key:
            result["api_key"] = api_key
        if model_type == "embedding":
            result.update({
                "dimension": config["dimension"],
                "batch_size": config["batch_size"],
                "supported_dimensions": config["supported_dimensions"],
                "max_batch_size": config["max_batch_size"],
                "max_retries": config["max_retries"],
                "retry_backoff": config["retry_backoff"],
            })
        else:
            result.update({
                "timeout": config["timeout_seconds"],
                "max_retries": config["max_retries"],
                "retry_backoff": config["retry_backoff"],
                "truncation": {
                    "strategy": config["truncation_strategy"],
                    "max_doc_chars": config["max_doc_chars"],
                    "monitor_threshold": config["monitor_threshold"],
                },
            })
        return result


_resolver = BoundModelResolver()


def get_bound_model_resolver() -> BoundModelResolver:
    return _resolver
