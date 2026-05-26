"""精确缓存 Pre / Post 中间件.

Pre: ExactCacheMiddleware
    命中 → 返回缓存 LLMResponse (短路, 不调 LLM); 标记 cache_hit=True / cache_type="exact"

Post: CacheWriteMiddleware
    成功调用后写缓存 (满足缓存条件时); 已 cache_hit 的不重复写
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from ..caching.exact_cache import ExactCacheBackend, get_exact_cache, make_cache_key
from ..request import LLMRequest, LLMResponse
from .base import PostMiddleware, PreMiddleware

logger = logging.getLogger(__name__)


def _should_cache(req: LLMRequest) -> bool:
    """是否允许缓存本次请求.

    要求:
        - cache_enabled
        - temperature == 0 (确定性)
        - 无 tools (Phase 4 工具调用先不缓存)
        - 有 preferred_provider/model (需要稳定 key)
    """
    if not req.cache_enabled:
        return False
    if req.temperature is None or req.temperature > 0:
        return False
    if req.tools:
        return False
    if not req.preferred_provider or not req.preferred_model:
        return False
    return True


def _cache_key_for(req: LLMRequest) -> str:
    return make_cache_key(
        req.preferred_provider or "",
        req.preferred_model or "",
        req.messages,
    )


def _cache_ttl_for(req: LLMRequest) -> int:
    ttl_override = (req.extra_options or {}).get("cache_ttl") if req.extra_options else None
    if isinstance(ttl_override, int) and ttl_override > 0:
        return ttl_override
    return 3600


@dataclass
class ExactCacheMiddleware(PreMiddleware):
    """命中即短路返回, 不进入 LLMDispatcher."""

    backend: ExactCacheBackend | None = None

    def _backend(self) -> ExactCacheBackend:
        return self.backend or get_exact_cache()

    async def process(self, req: LLMRequest) -> LLMResponse | None:
        if not _should_cache(req):
            return None
        key = _cache_key_for(req)
        try:
            cached = await self._backend().get(key)
        except Exception:  # noqa: BLE001
            logger.exception("精确缓存查询失败 (已忽略)")
            return None
        if cached is None:
            return None
        logger.debug("LLM 精确缓存命中: provider=%s model=%s", req.preferred_provider, req.preferred_model)
        return replace(cached, cache_hit=True, cache_type="exact")


@dataclass
class CacheWriteMiddleware(PostMiddleware):
    """写缓存. cache_hit 已为 True 的不重复写."""

    backend: ExactCacheBackend | None = None

    def _backend(self) -> ExactCacheBackend:
        return self.backend or get_exact_cache()

    async def process(self, req: LLMRequest, resp: LLMResponse) -> LLMResponse:
        if resp.cache_hit:
            return resp
        if not _should_cache(req):
            return resp
        if not resp.content:
            # 空 content 不写缓存 (避免污染流式中转 / 错误结果)
            return resp
        key = _cache_key_for(req)
        try:
            await self._backend().set(key, resp, ttl_seconds=_cache_ttl_for(req))
        except Exception:  # noqa: BLE001
            logger.exception("精确缓存写入失败 (已忽略)")
        return resp


__all__ = ["CacheWriteMiddleware", "ExactCacheMiddleware"]
