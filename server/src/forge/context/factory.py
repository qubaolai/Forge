"""ContextBuilder 装配入口.

约定:
    - memory_store / token_counter 是 "长寿" 单例, 模块级缓存.
    - history_repo 持有 DB session, 必须按请求 new (因此作为参数传入).

记忆系统接入:
    settings.memory.enabled=true 且 DB 已初始化 -> CompositeMemoryStore(SummaryStore)
    否则 -> NullMemoryStore (脚本 / 单测场景安全)
"""

from __future__ import annotations

import logging

from config.settings import get_settings

from forge.context.base import ContextBuilder
from forge.context.builder import CompositeContextBuilder

# Storage protocol injected, swap by deployment_mode (S6.5 M3).
from forge.infrastructure.storage import MessageStore, make_summary_store
from forge.llm.token_counter import TokenCounter, get_token_counter
from forge.memory.base import MemoryStore
from forge.memory.null import NullMemoryStore

logger = logging.getLogger(__name__)

_DEFAULT_MEMORY_STORE: MemoryStore | None = None


def get_memory_store() -> MemoryStore:
    """返回全局 MemoryStore 单例.

    memory.enabled=false / DB 未初始化 -> NullMemoryStore (静默, 不抛).
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
        from forge.infrastructure.database.database import (
            get_session_factory,
        )
        from forge.memory.composite import CompositeMemoryStore

        factory = get_session_factory()
        _DEFAULT_MEMORY_STORE = CompositeMemoryStore(
            summary_store=make_summary_store(factory),
        )
        logger.info("MemoryStore 就绪: CompositeMemoryStore (仅摘要)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("MemoryStore 装配失败, 降级到 NullMemoryStore: %s", exc)
        _DEFAULT_MEMORY_STORE = NullMemoryStore()

    return _DEFAULT_MEMORY_STORE


def reset_memory_store() -> None:
    """单测用."""
    global _DEFAULT_MEMORY_STORE
    _DEFAULT_MEMORY_STORE = None


def build_context_builder(
    history_repo: MessageStore,
    *,
    memory_store: MemoryStore | None = None,
    token_counter: TokenCounter | None = None,
) -> ContextBuilder:
    """组装一个 ContextBuilder.

    Args:
        history_repo: 当前请求持有的 MessageStore (含 DB session).
        memory_store: 可选, 默认走 get_memory_store().
        token_counter: 可选, 默认走 get_token_counter().
    """
    return CompositeContextBuilder(
        history_repo=history_repo,
        memory_store=memory_store or get_memory_store(),
        token_counter=token_counter or get_token_counter(),
    )
