"""模型调用链服务 — 对话链 / 档位链 的读取与保存(含校验 + 缓存热更新)。

保存时逐条校验 (provider, model):provider 启用、model 启用且属于该 provider、
model_type=chat;对话链额外要求 entry.provider == chain_key。任一不合法整组拒绝。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.repositories.model_chain_repo import (
    SCOPE_CONVERSATION,
    SCOPE_TIER,
    TIER_KEYS,
    ModelChainRepository,
)
from forge.infrastructure.database.repositories.model_provider_repo import ProviderRepository
from forge.infrastructure.database.repositories.model_repo import ModelRepository
from forge.llm.model_config_cache import ModelConfigCache

VALID_SCOPES = (SCOPE_CONVERSATION, SCOPE_TIER)


class ModelChainService:
    def __init__(self, db: AsyncSession, model_cache: ModelConfigCache | None = None) -> None:
        self.db = db
        self._cache = model_cache or ModelConfigCache.get_global()

    @staticmethod
    def _row_dict(row) -> dict:
        return {
            "id": str(row.id),
            "scope": row.scope,
            "chain_key": row.chain_key,
            "entries": row.entries or [],
            "version": row.version,
        }

    async def list_chains(self, scope: str) -> list[dict]:
        if scope not in VALID_SCOPES:
            raise ValueError(f"未知链类型: {scope}")
        rows = await ModelChainRepository(self.db).list_by_scope(scope)
        return [self._row_dict(r) for r in rows]

    async def set_chain(
        self, scope: str, chain_key: str, entries: list[dict], *, updated_by: str | None = None
    ) -> dict:
        if scope not in VALID_SCOPES:
            raise ValueError(f"未知链类型: {scope}")
        if scope == SCOPE_TIER and chain_key not in TIER_KEYS:
            raise ValueError(f"未知档位: {chain_key},可选 {TIER_KEYS}")

        provider_repo = ProviderRepository(self.db)
        model_repo = ModelRepository(self.db)

        # 去重(保序)+ 逐条校验
        clean: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for e in entries:
            provider = (e.get("provider") or "").strip()
            model = (e.get("model") or "").strip()
            if not provider or not model:
                raise ValueError("链条目必须含 provider 和 model")
            if scope == SCOPE_CONVERSATION and provider != chain_key:
                raise ValueError(f"对话链条目的 provider({provider})必须等于 {chain_key}")
            prov = await provider_repo.get_by_name(provider)
            if prov is None or not prov.is_enabled:
                raise ValueError(f"供应商不存在或未启用: {provider}")
            target = await model_repo.get_by_biz_key(prov.id, "chat", model)
            if target is None or not target.is_enabled:
                raise ValueError(f"模型不存在/未启用/非chat: {provider}:{model}")
            key = (provider, model)
            if key in seen:
                continue
            seen.add(key)
            clean.append({"provider": provider, "model": model})

        row = await ModelChainRepository(self.db).upsert(
            scope, chain_key, clean, updated_by=updated_by
        )
        # 热更新缓存(同一 session 内,upsert 已 flush 可见)
        await self._cache.reload_all(self.db)
        return self._row_dict(row)
