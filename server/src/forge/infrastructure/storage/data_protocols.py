"""Storage 抽象层 — 数据持久化 Protocol 契约 (S6.5 M3).

设计目的:
- 把业务代码 (chat / memory / api 路由 / kb 服务) 跟具体存储实现解耦.
- 业务侧只 import 本模块的 Protocol; 具体类由 ``data_factory.py`` 装配.
- 单机模式下 Protocol 满足者是现有的 ``*Repository`` / ``*Log`` / ``SummaryStore`` 等.
- 未来 SaaS 模式接入 RDS / S3 / Dynamo 时新增实现类, 业务代码不动.

不引入新能力:
- 每个 Protocol 的方法签名严格按 **当前业务实际调用** 列出. 不是完整 ORM.
- 现有的方法签名 (含 default 参数, 关键字参数) 全部保留.

约定:
- Python ``Protocol`` 是结构化匹配, 现有具体类无需显式 ``class X(Y):`` 继承,
  签名匹配即满足 ``isinstance`` (启用 ``@runtime_checkable`` 后).
- 复杂返回类型 (含 ORM 实例) 用 ``TYPE_CHECKING`` 块 import 减少耦合.

与既有 ``base.py:FileStorage`` 的区分:
- ``FileStorage``: blob 文件存储 (KB 上传文件落盘).
- 本模块: 结构化数据存储 (session / message / KB metadata / 摘要 / 成本 / 审计).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from forge.infrastructure.audit_log import AuditEntry
    from forge.infrastructure.cost_log import CostEntry
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
    from forge.infrastructure.database.orm.knowledge_base_orm import (
        KnowledgeBaseOrm,
    )
    from forge.infrastructure.database.repositories.message_repo import (
        ChatMessageView,
    )
    from forge.infrastructure.database.repositories.session_repo import (
        SessionView,
    )
    from forge.memory.base import Summary


# ---------------------------------------------------------------------------
# 1. SessionStore  ←  SessionRepository (本地实现: JSONL SessionLog 文件)
# ---------------------------------------------------------------------------
@runtime_checkable
class SessionStore(Protocol):
    """会话元数据存储."""

    async def list_by_user(
        self,
        user_id: str,
        page: int,
        page_size: int,
        q: str = "",
    ) -> tuple[Sequence[SessionView], int]: ...

    async def get_by_id(self, session_id: str) -> SessionView | None: ...

    async def create(
        self,
        *,
        user_id: str,
        agent_id: str = "default",
        title: str | None = None,
    ) -> SessionView: ...

    async def update_title(self, session: SessionView, title: str) -> SessionView: ...

    async def delete(self, session: SessionView) -> None: ...


# ---------------------------------------------------------------------------
# 2. MessageStore  ←  MessageRepository (本地实现: JSONL SessionLog 内的 message 段)
# ---------------------------------------------------------------------------
@runtime_checkable
class MessageStore(Protocol):
    """会话消息存储."""

    async def list_by_session(
        self,
        session_id: str,
        page: int,
        page_size: int,
    ) -> tuple[Sequence[ChatMessageView], int]: ...

    async def load_recent(
        self,
        session_id: str,
        limit: int = 30,
    ) -> list[ChatMessageView]: ...

    async def add(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        status: str = "done",
        parent_id: str | None = None,
    ) -> ChatMessageView: ...

    async def get_by_id(self, message_id: str) -> ChatMessageView | None: ...

    async def count_by_session(self, session_id: str) -> int: ...

    async def count_by_sessions(self, session_ids: list[str]) -> dict[str, int]: ...

    async def latest_at_by_sessions(
        self,
        session_ids: list[str],
    ) -> dict[str, datetime]: ...

    async def save(self, msg: ChatMessageView) -> ChatMessageView: ...

    async def update(
        self,
        msg: ChatMessageView,
        *,
        content: str | None = None,
        status: str | None = None,
        citations: list | None = None,
        tool_calls: list | None = None,
        usage: dict | None = None,
        error_message: str | None = None,
        context_meta: dict | None = None,
        reasoning_content: str | None = None,
        reasoning_duration_ms: int | None = None,
    ) -> ChatMessageView: ...

    async def delete_by_id(self, message_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# 3. CostStore  ←  CostLog (本地实现: cost.jsonl)
# ---------------------------------------------------------------------------
@runtime_checkable
class CostStore(Protocol):
    """LLM / RAG / 工具调用成本记录."""

    async def record(self, entry: CostEntry) -> None: ...

    def iter_all(self) -> AsyncIterator[CostEntry]: ...

    async def tail(self, n: int) -> list[CostEntry]: ...

    async def aggregate(
        self,
        group_by: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        include_errors: bool = False,
    ) -> dict[str, float]: ...

    async def total_usd(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> float: ...

    async def today_total_usd(self, *, tz: Any = UTC) -> float: ...


# ---------------------------------------------------------------------------
# 4. AuditStore  ←  AuditLog (本地实现: audit.jsonl)
# ---------------------------------------------------------------------------
@runtime_checkable
class AuditStore(Protocol):
    """危险工具调用 / 配置变更等审计记录."""

    async def record(self, entry: AuditEntry) -> None: ...

    def iter_all(self) -> AsyncIterator[AuditEntry]: ...

    async def tail(self, n: int) -> list[AuditEntry]: ...

    async def filter(
        self,
        *,
        tool: str | None = None,
        outcome: str | None = None,
        agent_role: str | None = None,
        workspace: str | None = None,
        session_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditEntry]: ...

    async def count(
        self,
        *,
        outcome: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int: ...


# ---------------------------------------------------------------------------
# 5. SummaryStore  ←  memory.summary.store.SummaryStore (本地实现: MySQL)
# ---------------------------------------------------------------------------
@runtime_checkable
class SummaryStore(Protocol):
    """会话长期摘要存储 (memory 模块的 stage 2 backend)."""

    async def get(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> Summary | None: ...

    async def upsert(
        self,
        *,
        session_id: str,
        workspace_id: str | None = None,
        content: str,
        covered_until_message_id: str | None,
        token_count: int,
    ) -> Summary: ...

    async def delete(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> None: ...


# ---------------------------------------------------------------------------
# 6. KnowledgeBaseStore  ←  KnowledgeBaseRepository (本地实现: SQLite kb.db)
# ---------------------------------------------------------------------------
@runtime_checkable
class KnowledgeBaseStore(Protocol):
    """KB 元数据存储."""

    async def create(self, kb: KnowledgeBaseOrm) -> KnowledgeBaseOrm: ...

    async def get(self, kb_id: str) -> KnowledgeBaseOrm | None: ...

    async def get_owned(
        self,
        kb_id: str,
        user_id: str,
    ) -> KnowledgeBaseOrm | None: ...

    async def list_for_user(self, user_id: str) -> list[KnowledgeBaseOrm]: ...

    async def delete(self, kb: KnowledgeBaseOrm) -> None: ...

    async def update_stats(
        self,
        kb_id: str,
        *,
        document_count_delta: int = 0,
        chunk_count_delta: int = 0,
        size_bytes_delta: int = 0,
    ) -> None: ...

    async def find_accessible_by_names(
        self,
        names: list[str],
        user_id: str,
    ) -> list[KnowledgeBaseOrm]: ...


# ---------------------------------------------------------------------------
# 7. KbDocumentStore  ←  KbDocumentRepository (本地实现: SQLite kb.db)
# ---------------------------------------------------------------------------
@runtime_checkable
class KbDocumentStore(Protocol):
    """KB 文档元数据存储 (chunk 不属于本 Protocol, 直走 vector / BM25 库)."""

    async def create(self, doc: KbDocumentOrm) -> KbDocumentOrm: ...

    async def update_status(
        self,
        doc_id: str,
        status: str,
        *,
        message: str | None = None,
        progress: int | None = None,
        chunk_count: int | None = None,
        mark_indexed: bool = False,
    ) -> KbDocumentOrm | None: ...

    async def delete(self, doc: KbDocumentOrm) -> None: ...

    async def get(self, doc_id: str) -> KbDocumentOrm | None: ...

    async def get_in_kb(
        self,
        doc_id: str,
        kb_id: str,
    ) -> KbDocumentOrm | None: ...

    async def list_by_kb(
        self,
        kb_id: str,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[KbDocumentOrm], int]: ...

    async def list_indexed_doc_ids(self, kb_ids: list[str]) -> list[str]: ...

    async def find_by_content_hash(
        self,
        kb_id: str,
        content_hash: str,
    ) -> KbDocumentOrm | None: ...


# ---------------------------------------------------------------------------
# 导出契约表 — 工厂层与测试用
# ---------------------------------------------------------------------------
ALL_PROTOCOLS: tuple[type, ...] = (
    SessionStore,
    MessageStore,
    CostStore,
    AuditStore,
    SummaryStore,
    KnowledgeBaseStore,
    KbDocumentStore,
)
