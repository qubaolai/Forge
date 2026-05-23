"""LLM SDK client 池 — 多 Key 支持 + weighted_round_robin + 429 冷却。

按 (impl, api_key) 缓存 client 实例, 跨请求复用。
每个 provider 可注册多个 key, 选择时过滤冷却中的 key, weighted round-robin。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .providers.base import LLM

logger = logging.getLogger(__name__)

LLMFactoryFn = Callable[[str, str, dict], LLM]


@dataclass
class _KeyEntry:
    api_key: str
    weight: int = 1
    cooldown_until: float = 0.0  # epoch seconds
    failure_score: int = 0
    _rr_counter: int = 0  # round-robin counter


class LLMClientPool:
    """LLM client 池, 按 (impl, api_key) 缓存 client。多 Key 支持 weighted round-robin。"""

    def __init__(self, factory: LLMFactoryFn) -> None:
        self._factory = factory
        self._instances: dict[tuple[str, str], LLM] = {}
        self._keys: dict[str, list[_KeyEntry]] = {}  # impl → keys
        self._lock = threading.Lock()

    # ---- Key 管理 ----
    def register_key(self, impl: str, api_key: str, weight: int = 1) -> None:
        """注册一个 API Key。已存在的 key 不重复注册。"""
        with self._lock:
            keys = self._keys.setdefault(impl, [])
            if not any(k.api_key == api_key for k in keys):
                keys.append(_KeyEntry(api_key=api_key, weight=weight))
                logger.info("Key 注册: impl=%s fingerprint=%s*** weight=%d", impl, api_key[:6], weight)

    def mark_cooldown(self, impl: str, api_key: str, seconds: float = 30.0) -> None:
        """429 后冷却指定 key。"""
        now = time.time()
        with self._lock:
            for k in self._keys.get(impl, []):
                if k.api_key == api_key:
                    k.cooldown_until = now + seconds
                    k.failure_score += 1
                    logger.warning("Key 冷却: impl=%s fingerprint=%s*** cooldown=%.0fs score=%d", impl, api_key[:6], seconds, k.failure_score)
                    return

    # ---- Key 选择 ----
    def _select_key(self, impl: str) -> _KeyEntry | None:
        """weighted round-robin: 过滤冷却中的 key, 选权重最高的下一个。"""
        keys = self._keys.get(impl, [])
        now = time.time()
        available = [k for k in keys if k.cooldown_until <= now]
        if not available:
            return None
        # 按 weight 降序, 然后 round-robin
        available.sort(key=lambda k: (-k.weight, k._rr_counter))
        selected = available[0]
        selected._rr_counter += 1
        return selected

    # ---- Client 获取 ----
    def get(self, impl: str, api_key: str, client_options: dict | None = None) -> LLM:
        """获取指定 (impl, api_key) 的 client（兼容旧接口）。"""
        key = (impl, api_key)
        if (inst := self._instances.get(key)) is not None:
            return inst
        with self._lock:
            if (inst := self._instances.get(key)) is not None:
                return inst
            inst = self._factory(impl, api_key, client_options or {})
            self._instances[key] = inst
            logger.info("LLM client 新建: impl=%s key=%s***", impl, api_key[:6] if api_key else "-")
            return inst

    def get_by_impl(self, impl: str, client_options: dict | None = None) -> LLM | None:
        """从注册的 key 中选一个可用 client。无可用 key 返回 None。"""
        entry = self._select_key(impl)
        if entry is None:
            return None
        return self.get(impl, entry.api_key, client_options)

    def warm(self, impl: str, api_key: str, client_options: dict | None = None) -> None:
        self.get(impl, api_key, client_options)

    def evict(self, impl: str, api_key: str) -> None:
        key = (impl, api_key)
        with self._lock:
            self._instances.pop(key, None)
            keys = self._keys.get(impl, [])
            self._keys[impl] = [k for k in keys if k.api_key != api_key]
        logger.info("LLM client 失效: impl=%s key=%s***", impl, api_key[:6])

    def clear(self) -> None:
        with self._lock:
            count = len(self._instances)
            self._instances.clear()
            self._keys.clear()
        logger.info("LLM client 池清空 %d 个实例", count)

    def size(self) -> int:
        return len(self._instances)

    def stats(self) -> list[dict]:
        result: list[dict] = []
        with self._lock:
            for impl, entries in self._keys.items():
                for e in entries:
                    result.append({
                        "impl": impl,
                        "key_fingerprint": f"{e.api_key[:6]}***",
                        "weight": e.weight,
                        "cooldown": max(0, e.cooldown_until - time.time()) if e.cooldown_until > time.time() else 0,
                        "failure_score": e.failure_score,
                    })
        return result


# ----------------------------------------------------------------------
# 全局单例
# ----------------------------------------------------------------------
_llm_pool: LLMClientPool | None = None
_init_lock = threading.Lock()


def get_llm_pool() -> LLMClientPool:
    global _llm_pool
    if _llm_pool is not None:
        return _llm_pool
    with _init_lock:
        if _llm_pool is not None:
            return _llm_pool
        from .gateway import build_llm_client
        _llm_pool = LLMClientPool(build_llm_client)
        return _llm_pool


__all__ = ["LLMClientPool", "get_llm_pool"]
