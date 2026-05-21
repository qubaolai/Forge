"""LLM SDK client 池.

按 (impl, api_key) 缓存 LLM client 实例 (SDK 内部持有 HTTP 连接池),
跨请求复用. 每个 (provider, key) 对建一次 SDK 实例, 后续 chat
调用从池里拿, 走同一个 HTTP keep-alive 连接池.

为什么 LLM 真的需要"池"而不只是"单例":
    - 同一 provider 支持多 api_key (round_robin / random / first 策略),
      每个 key 是独立 client.
    - key 失效时 evict 单个 client, 不影响其他 key.
    - per-request 解析 (impl, model) → 查活跃 key → 池里取 client.

embedding / reranker 是启动期固定单例, 不需要这个抽象 (见各自 factory.py).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .providers.base import LLM

logger = logging.getLogger(__name__)


LLMFactoryFn = Callable[[str, str, dict], LLM]


class LLMClientPool:
    """LLM client 池, 按 (impl, api_key) 缓存. 线程安全.

    用法:
        pool = LLMClientPool(build_llm_client)
        client = pool.get("dashscope", "sk-xxx", {"base_url": None, "timeout": 30})
    """

    def __init__(self, factory: LLMFactoryFn) -> None:
        self._factory = factory
        self._instances: dict[tuple[str, str], LLM] = {}
        self._lock = threading.Lock()

    def get(
        self,
        impl: str,
        api_key: str,
        client_options: dict | None = None,
    ) -> LLM:
        key = (impl, api_key)
        # 快路径
        if (inst := self._instances.get(key)) is not None:
            return inst
        # 慢路径
        with self._lock:
            if (inst := self._instances.get(key)) is not None:
                return inst
            inst = self._factory(impl, api_key, client_options or {})
            self._instances[key] = inst
            logger.info(
                "LLM client 新建: impl=%s key=%s***",
                impl,
                api_key[:6] if api_key else "-",
            )
            return inst

    def warm(
        self,
        impl: str,
        api_key: str,
        client_options: dict | None = None,
    ) -> None:
        """启动期主动加载. 失败 (api_key 无效等) 由调用方处理."""
        self.get(impl, api_key, client_options)

    def evict(self, impl: str, api_key: str) -> None:
        """显式失效一个 client. 改 api_key 后调用."""
        key = (impl, api_key)
        with self._lock:
            removed = self._instances.pop(key, None)
        if removed is not None:
            logger.info("LLM client 失效: impl=%s key=%s***", impl, api_key[:6])

    def clear(self) -> None:
        """清空所有缓存. 关停或整体热重载时用."""
        with self._lock:
            count = len(self._instances)
            self._instances.clear()
        logger.info("LLM client 池清空 %d 个实例", count)

    def size(self) -> int:
        return len(self._instances)

    def stats(self) -> list[dict]:
        """监控/调试: 当前池中实例列表 (脱敏)."""
        return [
            {"impl": impl, "key_prefix": f"{k[:6]}***" if k else "-"}
            for (impl, k) in sorted(self._instances.keys())
        ]


# ----------------------------------------------------------------------
# 全局单例
# ----------------------------------------------------------------------
_llm_pool: LLMClientPool | None = None
_init_lock = threading.Lock()


def get_llm_pool() -> LLMClientPool:
    """返回全局 LLM client 池单例.

    懒构造避免 import 期循环 (gateway → client_pool → gateway).
    """
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
