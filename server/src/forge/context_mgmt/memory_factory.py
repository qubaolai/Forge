"""MemoryStore 装配入口（从旧 forge.context.factory 迁入）。

约定:
    - memory_store 是 "长寿" 单例, 模块级缓存。

记忆系统接入:
    settings.memory.enabled=true 且 DB 已初始化 -> CompositeMemoryStore(SummaryStore)
    否则 -> NullMemoryStore (脚本 / 单测场景安全)
"""

from __future__ import annotations

import logging

from forge.config.settings import get_settings
from forge.memory.base import MemoryStore
from forge.memory.null import NullMemoryStore
from forge.memory.summary.store import SummaryStore

logger = logging.getLogger(__name__)

_DEFAULT_MEMORY_STORE: MemoryStore | None = None


def get_memory_store() -> MemoryStore:
    """返回全局 MemoryStore 单例。

    memory.enabled=false / DB 未初始化 -> NullMemoryStore (静默, 不抛)。
    """
    global _DEFAULT_MEMORY_STORE
    if _DEFAULT_MEMORY_STORE is not None:
        return _DEFAULT_MEMORY_STORE

    try:
        settings = get_settings()
        if not settings.memory.enabled:
            _DEFAULT_MEMORY_STORE = NullMemoryStore()
            return _DEFAULT_MEMORY_STORE
    except Exception:  # noqa: BLE001
        # settings 未配置时也走 NullMemoryStore
        _DEFAULT_MEMORY_STORE = NullMemoryStore()
        return _DEFAULT_MEMORY_STORE

    # 尝试装 CompositeMemoryStore; DB 未初始化时降级
    try:
        from forge.infrastructure.database.database import get_session_factory
        from forge.memory.composite import CompositeMemoryStore

        factory = get_session_factory()
        _DEFAULT_MEMORY_STORE = CompositeMemoryStore(
            summary_store=SummaryStore(factory),
        )
        logger.info("MemoryStore 就绪: CompositeMemoryStore (仅摘要)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("MemoryStore 装配失败, 降级到 NullMemoryStore: %s", exc)
        _DEFAULT_MEMORY_STORE = NullMemoryStore()

    return _DEFAULT_MEMORY_STORE


def reset_memory_store() -> None:
    """单测用。"""
    global _DEFAULT_MEMORY_STORE
    _DEFAULT_MEMORY_STORE = None
