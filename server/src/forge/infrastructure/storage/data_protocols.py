"""Storage 抽象层 — 数据持久化 ABC 契约 (S6.5 M3).

设计目的:
- 把业务代码 (chat / memory / api 路由 / kb 服务) 跟具体存储实现解耦.
- 业务侧只 import 本模块的 ABC; 具体类由工厂层装配.
- MySQL 模式下具体实现是 ChatSessionRepository / ChatMessageRepository 等.

不引入新能力:
- 每个 ABC 的方法签名严格按 **当前业务实际调用** 列出. 不是完整 ORM.
- 现有的方法签名 (含 default 参数, 关键字参数) 全部保留.

约定:
- 具体实现必须显式继承对应 ABC；缺少抽象方法时实例化会立即失败.
- 复杂返回类型 (含 ORM 实例) 用 ``TYPE_CHECKING`` 块 import 减少耦合.

与既有 ``base.py:FileStorage`` 的区分:
- ``FileStorage``: blob 文件存储 (KB 上传文件落盘).
- 本模块: 结构化数据存储 (session / message / KB metadata / 摘要 / 成本 / 审计).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.infrastructure.audit_log import AuditEntry
    from forge.infrastructure.cost_log import CostEntry
    from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
    from forge.infrastructure.database.orm.knowledge_base_orm import (
        KnowledgeBaseOrm,
    )
    from forge.memory.base import Summary


# ---------------------------------------------------------------------------
# 共享数据视图 — 业务层与存储层之间的传输对象
# ---------------------------------------------------------------------------
@dataclass
class SessionView:
    """会话元数据视图。"""

    id: str
    user_id: str
    agent_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    last_message_at: datetime | None = None
    run_ids: list[str] | None = None


@dataclass
class ChatMessageView:
    """消息视图。"""

    id: str
    session_id: str
    role: str
    content: str
    status: str
    citations: Any | None = None
    tool_calls: Any | None = None
    usage: dict[str, Any] | None = None
    context_meta: dict[str, Any] | None = None
    parent_id: str | None = None
    error_message: str | None = None
    reasoning_content: str | None = None
    reasoning_duration_ms: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# 1. SessionStore  ←  SessionRepository (本地实现: JSONL SessionLog 文件)
# ---------------------------------------------------------------------------
class SessionStore(ABC):
    """会话元数据存储."""

    @abstractmethod
    async def list_by_user(
        self,
        user_id: str,
        page: int,
        page_size: int,
        q: str = "",
    ) -> tuple[Sequence[SessionView], int]: ...

    @abstractmethod
    async def get_by_id(self, session_id: str) -> SessionView | None: ...

    @abstractmethod
    async def create(
        self,
        *,
        user_id: str,
        title: str | None = None,
    ) -> SessionView: ...

    @abstractmethod
    async def update_title(self, session: SessionView, title: str) -> SessionView: ...

    @abstractmethod
    async def delete(self, session: SessionView) -> None: ...


# ---------------------------------------------------------------------------
# 2. MessageStore  ←  MessageRepository (本地实现: JSONL SessionLog 内的 message 段)
# ---------------------------------------------------------------------------
class MessageStore(ABC):
    """会话消息存储."""

    @abstractmethod
    async def list_by_session(
        self,
        session_id: str,
        page: int,
        page_size: int,
    ) -> tuple[Sequence[ChatMessageView], int]: ...

    @abstractmethod
    async def load_recent(
        self,
        session_id: str,
        limit: int = 30,
    ) -> list[ChatMessageView]: ...

    @abstractmethod
    async def add(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        status: str = "done",
        parent_id: str | None = None,
    ) -> ChatMessageView: ...

    @abstractmethod
    async def get_by_id(self, message_id: str) -> ChatMessageView | None: ...

    @abstractmethod
    async def count_by_session(self, session_id: str) -> int: ...

    @abstractmethod
    async def count_by_sessions(self, session_ids: list[str]) -> dict[str, int]: ...

    @abstractmethod
    async def latest_at_by_sessions(
        self,
        session_ids: list[str],
    ) -> dict[str, datetime]: ...

    @abstractmethod
    async def save(self, msg: ChatMessageView) -> ChatMessageView: ...

    @abstractmethod
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

    @abstractmethod
    async def delete_by_id(self, message_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# 3. CostStore  ←  CostLog (本地实现: cost.jsonl)
# ---------------------------------------------------------------------------
class CostStore(ABC):
    """LLM / RAG / 工具调用成本记录."""

    @abstractmethod
    async def record(self, entry: CostEntry) -> None: ...

    @abstractmethod
    def iter_all(self) -> AsyncIterator[CostEntry]: ...

    @abstractmethod
    async def tail(self, n: int) -> list[CostEntry]: ...

    @abstractmethod
    async def aggregate(
        self,
        group_by: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        include_errors: bool = False,
    ) -> dict[str, float]: ...

    @abstractmethod
    async def total_usd(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> float: ...

    @abstractmethod
    async def today_total_usd(self, *, tz: Any = UTC) -> float: ...


# ---------------------------------------------------------------------------
# 4. AuditStore  ←  AuditLog (本地实现: audit.jsonl)
# ---------------------------------------------------------------------------
class AuditStore(ABC):
    """危险工具调用 / 配置变更等审计记录."""

    @abstractmethod
    async def record(self, entry: AuditEntry) -> None: ...

    @abstractmethod
    def iter_all(self) -> AsyncIterator[AuditEntry]: ...

    @abstractmethod
    async def tail(self, n: int) -> list[AuditEntry]: ...

    @abstractmethod
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

    @abstractmethod
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
class SummaryStore(ABC):
    """会话长期摘要存储 (memory 模块的 stage 2 backend)."""

    @abstractmethod
    async def get(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> Summary | None: ...

    @abstractmethod
    async def upsert(
        self,
        *,
        session_id: str,
        workspace_id: str | None = None,
        content: str,
        covered_until_message_id: str | None,
        token_count: int,
    ) -> Summary: ...

    @abstractmethod
    async def delete(
        self,
        session_id: str,
        *,
        workspace_id: str | None = None,
    ) -> None: ...


# ---------------------------------------------------------------------------
# 6. KnowledgeBaseStore  ←  KnowledgeBaseRepository (本地实现: SQLite kb.db)
# ---------------------------------------------------------------------------
class KnowledgeBaseStore(ABC):
    """KB 元数据存储."""

    @abstractmethod
    async def create(self, kb: KnowledgeBaseOrm) -> KnowledgeBaseOrm: ...

    @abstractmethod
    async def get(self, kb_id: str) -> KnowledgeBaseOrm | None: ...

    @abstractmethod
    async def get_owned(
        self,
        kb_id: str,
        user_id: str,
    ) -> KnowledgeBaseOrm | None: ...

    @abstractmethod
    async def list_for_user(self, user_id: str) -> list[KnowledgeBaseOrm]: ...

    @abstractmethod
    async def delete(self, kb: KnowledgeBaseOrm) -> None: ...

    @abstractmethod
    async def update_stats(
        self,
        kb_id: str,
        *,
        document_count_delta: int = 0,
        chunk_count_delta: int = 0,
        size_bytes_delta: int = 0,
    ) -> None: ...

    @abstractmethod
    async def find_accessible_by_names(
        self,
        names: list[str],
        user_id: str,
    ) -> list[KnowledgeBaseOrm]: ...


# ---------------------------------------------------------------------------
# 7. KbDocumentStore  ←  KbDocumentRepository (本地实现: SQLite kb.db)
# ---------------------------------------------------------------------------
class KbDocumentStore(ABC):
    """KB 文档元数据存储 (chunk 不属于本 ABC, 直走 vector / BM25 库)."""

    @abstractmethod
    async def create(self, doc: KbDocumentOrm) -> KbDocumentOrm: ...

    @abstractmethod
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

    @abstractmethod
    async def delete(self, doc: KbDocumentOrm) -> None: ...

    @abstractmethod
    async def get(self, doc_id: str) -> KbDocumentOrm | None: ...

    @abstractmethod
    async def get_in_kb(
        self,
        doc_id: str,
        kb_id: str,
    ) -> KbDocumentOrm | None: ...

    @abstractmethod
    async def list_by_kb(
        self,
        kb_id: str,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[KbDocumentOrm], int]: ...

    @abstractmethod
    async def list_indexed_doc_ids(self, kb_ids: list[str]) -> list[str]: ...

    @abstractmethod
    async def find_by_content_hash(
        self,
        kb_id: str,
        content_hash: str,
    ) -> KbDocumentOrm | None: ...


# ---------------------------------------------------------------------------
# 导出契约表 — 工厂层与测试用
# ---------------------------------------------------------------------------
ALL_STORE_BASES: tuple[type, ...] = (
    SessionStore,
    MessageStore,
    CostStore,
    AuditStore,
    SummaryStore,
    KnowledgeBaseStore,
    KbDocumentStore,
)

ALL_PROTOCOLS = ALL_STORE_BASES
