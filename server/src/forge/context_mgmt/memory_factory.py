"""MemoryStore 装配入口（从旧 forge.context.factory 迁入）。

约定:
    - memory_store / fact_store 都是 "长寿" 单例, 模块级缓存。

记忆系统接入:
    settings.memory.enabled=true 且 DB 已初始化 -> CompositeMemoryStore(SummaryStore [+ FactStore])
    其中 FactStore 仅在 settings.memory.facts.enabled=true 时装配 (用户长期事实召回)
    否则 -> NullMemoryStore (脚本 / 单测场景安全)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from forge.config.settings import get_settings
from forge.memory.base import MemoryStore
from forge.memory.null import NullMemoryStore
from forge.memory.summary.store import SummaryStore

if TYPE_CHECKING:
    from forge.memory.facts.store import FactStore

logger = logging.getLogger(__name__)

_DEFAULT_MEMORY_STORE: MemoryStore | None = None
_DEFAULT_FACT_STORE: FactStore | None = None


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
        fact_store = get_fact_store()
        _DEFAULT_MEMORY_STORE = CompositeMemoryStore(
            summary_store=SummaryStore(factory),
            fact_store=fact_store,
        )
        logger.info(
            "MemoryStore 就绪: CompositeMemoryStore (%s)",
            "摘要 + 事实" if fact_store is not None else "仅摘要",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("MemoryStore 装配失败, 降级到 NullMemoryStore: %s", exc)
        _DEFAULT_MEMORY_STORE = NullMemoryStore()

    return _DEFAULT_MEMORY_STORE


def get_fact_store() -> FactStore | None:
    """返回全局 FactStore 单例; facts 关闭 / 装配失败返回 None。

    供 CompositeMemoryStore 读路径与将来的事实管理 API 复用同一实例。
    """
    global _DEFAULT_FACT_STORE
    if _DEFAULT_FACT_STORE is not None:
        return _DEFAULT_FACT_STORE

    try:
        settings = get_settings()
        if not (settings.memory.enabled and settings.memory.facts.enabled):
            return None
        from forge.infrastructure.database.database import get_session_factory
        from forge.memory.facts.service import build_fact_store

        _DEFAULT_FACT_STORE = build_fact_store(
            get_session_factory(), settings.memory.facts
        )
        logger.info("FactStore 就绪 (用户长期事实召回已启用)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("FactStore 装配失败, 事实召回关闭: %s", exc)
        return None
    return _DEFAULT_FACT_STORE


def reset_memory_store() -> None:
    """单测用。"""
    global _DEFAULT_MEMORY_STORE, _DEFAULT_FACT_STORE
    _DEFAULT_MEMORY_STORE = None
    _DEFAULT_FACT_STORE = None
