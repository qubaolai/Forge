"""LLM SDK client 池 — 多 Key 支持 + smooth weighted round-robin + 429 冷却。

按 (impl, api_key) 缓存 client 实例, 跨请求复用。
每个 provider 可注册多个 key, 选择时过滤冷却中的 key, 再做平滑加权轮询。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .providers.base import LLM

logger = logging.getLogger(__name__)

LLMFactoryFn = Callable[[str, str, dict], LLM]


@dataclass
class _KeyEntry:
    api_key: str
    weight: int = 1
    cooldown_until: float = 0.0  # epoch seconds
    failure_score: int = 0
    current_weight: int = 0


class KeySelectionStrategy(Protocol):
    """Key 选择策略扩展点。"""

    def select(self, entries: list[_KeyEntry]) -> _KeyEntry | None:
        """从可用 Key 中选择一个。"""


class WeightedRoundRobinStrategy:
    """平滑加权轮询，避免简单排序导致高权重 Key 长期独占。"""

    def select(self, entries: list[_KeyEntry]) -> _KeyEntry | None:
        if not entries:
            return None
        total_weight = sum(entry.weight for entry in entries)
        selected: _KeyEntry | None = None
        for entry in entries:
            entry.current_weight += entry.weight
            if selected is None or entry.current_weight > selected.current_weight:
                selected = entry
        assert selected is not None
        selected.current_weight -= total_weight
        return selected


class LLMClientPool:
    """LLM client 池, 按 (impl, api_key) 缓存 client。多 Key 支持 weighted round-robin。"""

    def __init__(
        self,
        factory: LLMFactoryFn,
        selector: KeySelectionStrategy | None = None,
    ) -> None:
        self._factory = factory
        self._instances: dict[tuple[str, str], LLM] = {}
        self._keys: dict[str, list[_KeyEntry]] = {}  # impl → keys
        self._lock = threading.Lock()
        self._selector = selector or WeightedRoundRobinStrategy()

    @staticmethod
    def _normalize_weight(weight: int | float | str | None) -> int:
        try:
            value = int(weight if weight is not None else 1)
        except (TypeError, ValueError):
            logger.warning("Key 权重非法，已归一为 1: weight=%r", weight)
            return 1
        if value <= 0:
            logger.warning("Key 权重必须大于 0，已归一为 1: weight=%r", weight)
            return 1
        return value

    # ---- Key 管理 ----
    def register_key(self, impl: str, api_key: str, weight: int = 1) -> None:
        """注册一个 API Key。已存在的 key 不重复注册。"""
        normalized_weight = self._normalize_weight(weight)
        with self._lock:
            keys = self._keys.setdefault(impl, [])
            existing = next((k for k in keys if k.api_key == api_key), None)
            if existing is not None:
                if existing.weight != normalized_weight:
                    existing.weight = normalized_weight
                    existing.current_weight = 0
                    logger.info(
                        "Key 权重更新: impl=%s fingerprint=%s*** weight=%d",
                        impl,
                        api_key[:6],
                        normalized_weight,
                    )
                return
            keys.append(_KeyEntry(api_key=api_key, weight=normalized_weight))
            logger.info(
                "Key 注册: impl=%s fingerprint=%s*** weight=%d",
                impl,
                api_key[:6],
                normalized_weight,
            )

    def mark_cooldown(self, impl: str, api_key: str, seconds: float = 30.0) -> None:
        """429 后冷却指定 key。"""
        now = time.time()
        with self._lock:
            for k in self._keys.get(impl, []):
                if k.api_key == api_key:
                    next_until = now + seconds
                    if k.cooldown_until > now:
                        k.cooldown_until = max(k.cooldown_until, next_until)
                        logger.warning(
                            "Key 已在冷却中，延长冷却时间: impl=%s fingerprint=%s*** cooldown=%.0fs score=%d",
                            impl,
                            api_key[:6],
                            max(0, k.cooldown_until - now),
                            k.failure_score,
                        )
                        return
                    k.cooldown_until = next_until
                    k.failure_score += 1
                    k.current_weight = 0
                    logger.warning(
                        "检测到 Key 限流，进入冷却: impl=%s fingerprint=%s*** cooldown=%.0fs score=%d",
                        impl,
                        api_key[:6],
                        seconds,
                        k.failure_score,
                    )
                    return
        logger.warning("Key 冷却失败，池中未找到 Key: impl=%s fingerprint=%s***", impl, api_key[:6])

    # ---- Key 选择 ----
    def _select_key(self, impl: str) -> _KeyEntry | None:
        """平滑加权轮询: 过滤冷却中的 key, 再按权重稳定分配。"""
        keys = self._keys.get(impl, [])
        now = time.time()
        available = [k for k in keys if k.cooldown_until <= now]
        if not available:
            return None
        selected = self._selector.select(available)
        if selected is not None:
            logger.info(
                "Key 选择完成: impl=%s fingerprint=%s*** weight=%d available=%d",
                impl,
                selected.api_key[:6],
                selected.weight,
                len(available),
            )
        return selected

    def _select_key_candidates(self, impl: str) -> list[_KeyEntry]:
        """返回本次请求的 key 候选: 首位按 WRR 选择，其余作为 429 key 级兜底。"""
        with self._lock:
            selected = self._select_key(impl)
            if selected is None:
                return []
            now = time.time()
            available = [
                k for k in self._keys.get(impl, [])
                if k.cooldown_until <= now and k.api_key != selected.api_key
            ]
            available.sort(key=lambda k: (-k.weight, k.api_key))
            return [selected, *available]

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
        with self._lock:
            entry = self._select_key(impl)
        if entry is None:
            return None
        return self.get(impl, entry.api_key, client_options)

    def get_by_impl_with_key(
        self, impl: str, client_options: dict | None = None
    ) -> tuple[LLM, str] | None:
        """从注册的 key 中选一个可用 client，并返回本次选择的明文 key。"""
        with self._lock:
            entry = self._select_key(impl)
        if entry is None:
            return None
        return self.get(impl, entry.api_key, client_options), entry.api_key

    def get_candidates_by_impl(
        self, impl: str, client_options: dict | None = None
    ) -> list[tuple[LLM, str]]:
        """返回本次请求可尝试的 client 列表，首个为 WRR 选中的 Key。"""
        entries = self._select_key_candidates(impl)
        return [(self.get(impl, entry.api_key, client_options), entry.api_key) for entry in entries]

    def warm(self, impl: str, api_key: str, client_options: dict | None = None) -> None:
        self.get(impl, api_key, client_options)

    def evict(self, impl: str, api_key: str) -> None:
        key = (impl, api_key)
        with self._lock:
            self._instances.pop(key, None)
            keys = self._keys.get(impl, [])
            self._keys[impl] = [k for k in keys if k.api_key != api_key]
        logger.info("LLM client 失效: impl=%s key=%s***", impl, api_key[:6])

    def reconcile_provider(
        self, impl: str, new_keys: list[dict], client_options: dict | None = None
    ) -> dict:
        """用 DB 中的 key 列表同步池状态：移除过期的，新增缺失的。

        Args:
            impl: provider 实现名
            new_keys: [{"api_key": "sk-xxx", "weight": 1}, ...]  明文 key 列表
            client_options: 建 client 用的参数

        Returns:
            {"added": N, "removed": N}
        """
        with self._lock:
            existing_entries = self._keys.get(impl, [])
            existing_keys = {e.api_key for e in existing_entries}
            new_key_set = {k["api_key"] for k in new_keys}
            new_weights = {
                k["api_key"]: self._normalize_weight(k.get("weight", 1))
                for k in new_keys
            }

            # 移除过期的
            removed = 0
            for old_key in existing_keys - new_key_set:
                self._instances.pop((impl, old_key), None)
                removed += 1
            self._keys[impl] = [e for e in existing_entries if e.api_key in new_key_set]
            for entry in self._keys[impl]:
                new_weight = new_weights.get(entry.api_key, 1)
                if entry.weight != new_weight:
                    entry.weight = new_weight
                    entry.current_weight = 0
                    logger.info(
                        "LLM client reconcile 更新权重: impl=%s key=%s*** weight=%d",
                        impl,
                        entry.api_key[:6],
                        new_weight,
                    )

        for old_key in existing_keys - new_key_set:
            logger.info("LLM client reconcile 移除: impl=%s key=%s***", impl, old_key[:6])

        # 新增缺失的（在锁外 warm，避免持有锁时做 HTTP 调用）
        added = 0
        for k in new_keys:
            if k["api_key"] not in existing_keys:
                self.register_key(impl, k["api_key"], weight=k.get("weight", 1))
                try:
                    self.warm(impl, k["api_key"], client_options or {})
                    added += 1
                except Exception:
                    logger.exception(
                        "LLM client reconcile 预热失败: impl=%s fingerprint=%s",
                        impl, k.get("fingerprint", "?")[:6],
                    )

        if added or removed:
            logger.info(
                "LLM client reconcile 完成: impl=%s added=%d removed=%d", impl, added, removed
            )
        return {"added": added, "removed": removed}

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
                        "current_weight": e.current_weight,
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
        from .registry import build_llm_client
        _llm_pool = LLMClientPool(build_llm_client)
        return _llm_pool


__all__ = [
    "LLMClientPool",
    "WeightedRoundRobinStrategy",
    "get_llm_pool",
]
