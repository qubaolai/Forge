"""模型同步服务：供应商拉取 -> DB 入库 -> Redis 全量重建。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from forge.core.crypto import resolve_key
from forge.llm.model_catalog import (
    LocalProviderPlaceholderError,
    ModelInfo,
    ModelIntersectionEmptyError,
    _fetch_models,
)
from forge.llm.model_config_cache import ModelConfigCache

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderSyncResult:
    provider: str
    models_synced: int
    models_created: int
    models_staled: int
    skipped_reason: str | None = None

    def to_dict(self) -> dict:
        payload = {
            "provider": self.provider,
            "models_synced": self.models_synced,
            "models_created": self.models_created,
            "models_staled": self.models_staled,
        }
        if self.skipped_reason:
            payload["skipped_reason"] = self.skipped_reason
        return payload


class ModelSyncService:
    """统一模型同步入口。"""

    def __init__(self, cache: ModelConfigCache) -> None:
        self._cache = cache

    async def sync_provider(
        self,
        db: AsyncSession,
        provider_name: str,
        *,
        reload_cache: bool = True,
    ) -> ProviderSyncResult:
        from forge.infrastructure.database.repositories.model_provider_repo import ProviderRepository
        from forge.infrastructure.database.repositories.model_repo import ModelRepository

        provider_repo = ProviderRepository(db)
        model_repo = ModelRepository(db)

        provider = await provider_repo.get_by_name(provider_name)
        if provider is None:
            raise ValueError(f"供应商不存在: {provider_name!r}")
        if not provider.is_enabled:
            raise ValueError(f"供应商已禁用，不能同步: {provider_name!r}")
        logger.info("开始执行模型同步 provider=%s", provider.name)

        keys = await provider_repo.list_enabled_keys(provider.id)
        api_key = ""
        for key_row in keys:
            api_key = resolve_key(key_row.key_ciphertext)
            if api_key:
                break
        logger.info("Provider Key 读取完成 provider=%s key_count=%d has_usable_key=%s", provider.name, len(keys), bool(api_key))

        provider_config = {
            "name": provider.name,
            "impl": provider.impl or provider.name,
            "api_key": api_key,
            "base_url": provider.base_url,
            "db_id": provider.id,
        }
        try:
            fetched = await _fetch_models(provider_config)
        except LocalProviderPlaceholderError as exc:
            logger.warning("本地 Provider 同步占位跳过 provider=%s reason=%s", provider.name, exc.reason)
            return ProviderSyncResult(
                provider=provider.name,
                models_synced=0,
                models_created=0,
                models_staled=0,
                skipped_reason=exc.reason,
            )
        except ModelIntersectionEmptyError:
            logger.warning("模型同步跳过: provider=%s OpenRouter 与 Provider 交集为空", provider.name)
            return ProviderSyncResult(
                provider=provider.name,
                models_synced=0,
                models_created=0,
                models_staled=0,
                skipped_reason="intersection_empty",
            )
        except Exception:
            logger.exception("模型拉取失败 provider=%s", provider.name)
            raise

        total = 0
        created = 0
        active_names: set[str] = set()
        for model in fetched:
            model_data = self._to_model_data(model)
            _, is_new = await model_repo.sync_upsert(provider.id, model_data)
            if is_new:
                created += 1
            total += 1
            active_names.add(model.name)

        staled = await model_repo.mark_stale(provider.id, active_names, model_type="text")
        await db.commit()
        if reload_cache:
            await self._cache.reload_all(db)

        logger.info(
            "模型同步完成 provider=%s total=%d created=%d staled=%d",
            provider.name,
            total,
            created,
            staled,
        )
        return ProviderSyncResult(
            provider=provider.name,
            models_synced=total,
            models_created=created,
            models_staled=staled,
        )

    async def sync_all_enabled(self, db: AsyncSession) -> list[ProviderSyncResult]:
        """同步所有启用供应商，单个失败不阻断其他供应商。"""
        from forge.infrastructure.database.repositories.model_provider_repo import ProviderRepository

        provider_repo = ProviderRepository(db)
        providers = await provider_repo.list_enabled()
        results: list[ProviderSyncResult] = []
        for provider in providers:
            try:
                result = await self.sync_provider(db, provider.name, reload_cache=False)
                results.append(result)
            except Exception:
                logger.exception("供应商模型同步失败 provider=%s", provider.name)
                await db.rollback()
        await self._cache.reload_all(db)
        return results

    @staticmethod
    def _to_model_data(model: ModelInfo) -> dict:
        thinking = model.thinking
        return {
            "name": model.name,
            "display_name": model.display_name,
            "model_type": model.model_type,
            "context_window": model.context_window,
            "max_output_tokens": model.max_output_tokens,
            "supports_tools": model.supports_tools,
            "supports_images": model.supports_images,
            "supports_thinking": thinking is not None,
            "thinking_type": thinking.type if thinking else None,
            "thinking_options": thinking.options if thinking else None,
            "thinking_default": thinking.default if thinking else None,
            "extra_params": model.extra_params,
        }
