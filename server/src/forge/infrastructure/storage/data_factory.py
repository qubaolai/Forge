"""Storage 装配工厂 (S6.5 M3).

唯一的具体类生产者: 业务代码不再 import ``MessageRepository`` 等具体类,
而是从本模块的 ``make_*`` 函数取 Protocol 类型的实例.

工厂职责:
1. 按 ``deployment_mode`` 选择 backend 实现 (local / sandbox / cloud).
2. local 模式: 返回现有 JSONL / SQLite / MySQL 具体实现.
3. sandbox / cloud 模式: 抛 ``NotImplementedError`` 并明示 "S6.5 阶段未实现".

不做的事:
- 不做长寿单例 (无状态工厂; SQLAlchemy session 由调用方提供).
- 不缓存实例 (cost/audit log 的 path 缓存在 ``default_*`` 工厂内已做).
- 不引入连接池 (DB engine pool 由 ``infrastructure/database/database.py`` 管).

部署模式检测:
- S6.5 M4 起默认从 ``settings.deployment_mode`` 读.
- 测试/脚本可用 env ``DEPLOYMENT_MODE`` 临时覆盖,与 settings 的环境展开保持一致.
- ``local`` (默认) 时所有 ``make_*`` 直接返回 Local 实现.
- 任何其它值 → 抛 ``NotImplementedError`` 并提示用户.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from .data_protocols import (
    AuditStore,
    CostStore,
    KbDocumentStore,
    KnowledgeBaseStore,
    MessageStore,
    SessionStore,
    SummaryStore,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

_SUPPORTED_DEPLOYMENT_MODES: frozenset[str] = frozenset({"local"})
_DEPLOYMENT_MODE_ENV: str = "DEPLOYMENT_MODE"


def get_deployment_mode() -> str:
    """读取当前部署模式.

    Returns:
        当前部署模式字符串. 默认 ``"local"``.
    """
    # 允许测试在 settings 已经初始化后临时 monkeypatch env; 生产路径下 env 会在
    # settings 加载时被展开进 deployment_mode.
    if _DEPLOYMENT_MODE_ENV in os.environ:
        return _normalize_mode(os.environ.get(_DEPLOYMENT_MODE_ENV))

    try:
        from config.settings import get_settings

        mode = getattr(get_settings(), "deployment_mode", "local")
        if isinstance(mode, str):
            return _normalize_mode(mode)
        logger.debug("settings.deployment_mode 非字符串, 回退 local: %r", mode)
        return "local"
    except Exception:  # noqa: BLE001
        logger.debug("读取 settings.deployment_mode 失败, 回退 local", exc_info=True)
        return "local"


def _normalize_mode(raw: str | None) -> str:
    return (raw or "local").strip().lower() or "local"


def _assert_local_mode(*, store_name: str) -> None:
    """非 local 模式下抛 NotImplementedError 并指明 S6.5 未实现."""
    mode = get_deployment_mode()
    if mode in _SUPPORTED_DEPLOYMENT_MODES:
        return
    raise NotImplementedError(
        f"deployment_mode={mode!r} 的 {store_name} backend 在 S6.5 阶段未实现 — "
        f"目前仅支持 {sorted(_SUPPORTED_DEPLOYMENT_MODES)}. "
        f"SaaS 模式 backend 由后续 milestone (RDS/S3 storage) 落地."
    )


# ---------------------------------------------------------------------------
# DB 会话相关 store (需要 per-request session 注入)
# ---------------------------------------------------------------------------
def make_session_store(db: AsyncSession) -> SessionStore:
    """装配 SessionStore (本地实现: JSONL SessionLog)."""
    _assert_local_mode(store_name="SessionStore")
    # 局部 import 避免本模块在 worker / 测试场景 eager 拉 SQLAlchemy.
    from forge.infrastructure.database.repositories.session_repo import (
        SessionRepository,
    )

    return SessionRepository(db)


def make_message_store(db: AsyncSession) -> MessageStore:
    """装配 MessageStore (本地实现: JSONL SessionLog 内的 message 段)."""
    _assert_local_mode(store_name="MessageStore")
    from forge.infrastructure.database.repositories.message_repo import (
        MessageRepository,
    )

    return MessageRepository(db)


def make_knowledge_base_store(db: AsyncSession) -> KnowledgeBaseStore:
    """装配 KnowledgeBaseStore (本地实现: SQLite kb.db)."""
    _assert_local_mode(store_name="KnowledgeBaseStore")
    from forge.infrastructure.database.repositories.knowledge_base_repo import (
        KnowledgeBaseRepository,
    )

    return KnowledgeBaseRepository(db)


def make_kb_document_store(db: AsyncSession) -> KbDocumentStore:
    """装配 KbDocumentStore (本地实现: SQLite kb.db)."""
    _assert_local_mode(store_name="KbDocumentStore")
    from forge.infrastructure.database.repositories.kb_document_repo import (
        KbDocumentRepository,
    )

    return KbDocumentRepository(db)


# ---------------------------------------------------------------------------
# 长寿 store (session_factory 注入 / 无 session)
# ---------------------------------------------------------------------------
def make_summary_store(session_factory: async_sessionmaker[AsyncSession]) -> SummaryStore:
    """装配 SummaryStore (本地实现: MySQL session_summaries 表)."""
    _assert_local_mode(store_name="SummaryStore")
    from forge.memory.summary.store import SummaryStore as LocalSummaryStore

    return LocalSummaryStore(session_factory)


def make_cost_store() -> CostStore:
    """装配 CostStore (本地实现: cost.jsonl)."""
    _assert_local_mode(store_name="CostStore")
    from forge.infrastructure.cost_log import default_cost_log

    return default_cost_log()


def make_audit_store() -> AuditStore:
    """装配 AuditStore (本地实现: audit.jsonl)."""
    _assert_local_mode(store_name="AuditStore")
    from forge.infrastructure.audit_log import default_audit_log

    return default_audit_log()
