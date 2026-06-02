"""会话日志 (session JSONL) — 取代 chat_sessions / chat_messages SQL 表.

对应方案文档:``单机化改造方案.md`` §5.2 + §6.3.

文件布局
--------
每个 session 一个独立 JSONL 文件:

    <global>/projects/<encoded-workspace>/sessions/{session_id}.jsonl

首行恒为 ``session_meta``, 之后每行是一条 ``message`` 或事件 record:

    {"type":"session_meta", "session_id":"sess_xxx", "title":"...", ...}
    {"type":"message", "id":"msg_1", "role":"user", "content":"...", "status":"done", ...}
    {"type":"message", "id":"msg_2", "role":"assistant", "content":"...partial", "status":"streaming"}
    {"type":"message", "id":"msg_2", "role":"assistant", "content":"...full", "status":"done"}
    ...

Supersede 语义
--------------
同一 ``id`` 写多次时, **后写覆盖前写** (snapshot 事件式).
``load_messages`` 会按 id 去重, 保留最新版本; 适用 streaming 期间多次落库
+ resume 流的常见场景.

读 / 写不要求强一致 — 进程被 kill 可能丢最末几行 (因为 fsync=False 默认),
但下一次重启不会让历史不可读 (JSONL 容错 corrupt 行).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.config import paths
from forge.infrastructure.jsonl import JsonlLog

__all__ = [
    "MessageRecord",
    "SessionLog",
    "SessionMeta",
    "find_session_log_path",
    "session_log_for",
]


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SessionMeta:
    """会话首行元数据 — 创建时一次性写, 之后不再变.

    ``title`` 可以通过单独的 ``meta_update`` 事件追加, 暂未实现.
    """

    session_id: str
    user_id: str | None = None
    workspace_path: str | None = None  # workspace.root_path 绝对路径
    title: str = "新会话"
    agent_id: str = "default"
    status: str = "active"  # active / deleted
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionMeta:
        kwargs = {k: v for k, v in d.items() if k != "type"}
        ts = kwargs.get("created_at")
        if isinstance(ts, str):
            kwargs["created_at"] = datetime.fromisoformat(ts)
        uts = kwargs.get("updated_at")
        if isinstance(uts, str):
            kwargs["updated_at"] = datetime.fromisoformat(uts)
        return cls(**kwargs)


@dataclass
class MessageRecord:
    """消息快照 — 与原 ChatMessage ORM 字段同构, 便于业务平移.

    ``status`` 可选值: pending / streaming / done / error / aborted / partial.
    同一 ``id`` 多次落库时, 后写覆盖前写.
    """

    id: str
    role: str  # user / assistant / system / tool
    content: str = ""
    status: str = "done"
    citations: list[Any] | None = None
    tool_calls: list[Any] | None = None
    usage: dict[str, Any] | None = None
    context_meta: dict[str, Any] | None = None
    parent_id: str | None = None
    error_message: str | None = None
    reasoning_content: str | None = None
    reasoning_duration_ms: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MessageRecord:
        kwargs = {k: v for k, v in d.items() if k != "type"}
        for key in ("created_at", "updated_at"):
            v = kwargs.get(key)
            if isinstance(v, str):
                kwargs[key] = datetime.fromisoformat(v)
        return cls(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# SessionLog
# ─────────────────────────────────────────────────────────────────────────────


class SessionLog:
    """单 session JSONL 文件的领域 wrapper.

    封装 ``JsonlLog`` 之上, 提供消息 / 元数据 / supersede 语义.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._log = JsonlLog(path)

    # ────────────────────── 元数据 ──────────────────────

    async def init(self, meta: SessionMeta) -> None:
        """写首行 session_meta. 调用方应保证只调一次 (新建 session 时).

        若文件已存在且非空, 仍会追加一条新的 session_meta — 调用方负责避免重复.
        """
        await self._log.append({"type": "session_meta", **meta.to_dict()})

    async def save_meta(self, meta: SessionMeta) -> None:
        """追加写一条 session_meta 快照 (后写覆盖前写)."""
        await self._log.append({"type": "session_meta", **meta.to_dict()})

    async def get_meta(self) -> SessionMeta | None:
        """读最新 session_meta 快照. 文件不存在或无 meta 行返回 None."""
        latest: SessionMeta | None = None
        async for r in self._log.iter_all():
            if r.get("type") == "session_meta":
                latest = SessionMeta.from_dict(r)
        return latest

    async def exists(self) -> bool:
        return self.path.exists()

    # ────────────────────── 消息 ──────────────────────

    async def append_message(self, msg: MessageRecord) -> None:
        """追加一条消息快照.

        若同 id 已写过, 这条作为新版本生效 (load_messages 会去重).
        典型场景: streaming 中途写 partial, 终态再写 done.
        """
        await self._log.append({"type": "message", **msg.to_dict()})

    async def load_messages(
        self, *, statuses: tuple[str, ...] | None = None
    ) -> list[MessageRecord]:
        """读所有消息, 按 id 去重 (supersede 语义), 按首次出现顺序返回.

        Args:
            statuses: 若指定, 只返回 status ∈ statuses 的消息.
                      例如 ``("done",)`` 用于构建 LLM 历史.
        """
        by_id: dict[str, MessageRecord] = {}
        order: list[str] = []
        async for r in self._iter_messages():
            mid = r.id
            if mid not in by_id:
                order.append(mid)
            by_id[mid] = r
        result = [by_id[i] for i in order]
        if statuses is not None:
            result = [m for m in result if m.status in statuses]
        return result

    async def tail_messages(self, n: int) -> list[MessageRecord]:
        """末尾 n 条消息 (去重后)."""
        if n <= 0:
            return []
        all_msgs = await self.load_messages()
        return all_msgs[-n:]

    async def load_recent(self, limit: int = 30, *, status: str = "done") -> list[MessageRecord]:
        """加载最近 limit 条指定状态的消息 (默认 'done').

        语义与原 ``MessageRepository.load_recent`` 对齐:
        用于构建 LLM 上下文, 只关心终态消息.
        """
        msgs = await self.load_messages(statuses=(status,))
        return msgs[-limit:] if limit > 0 else msgs

    async def get_message(self, message_id: str) -> MessageRecord | None:
        latest: MessageRecord | None = None
        async for r in self._iter_messages():
            if r.id == message_id:
                latest = r
        return latest

    async def count_messages(self) -> int:
        """去重后的消息数."""
        msgs = await self.load_messages()
        return len(msgs)

    # ────────────────────── 内部 ──────────────────────

    async def _iter_messages(self) -> AsyncIterator[MessageRecord]:
        async for r in self._log.iter_all():
            if r.get("type") != "message":
                continue
            yield MessageRecord.from_dict(r)


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────


def session_log_for(session_id: str, workspace_root: Path | None = None) -> SessionLog:
    """根据 session_id + workspace 解析路径.

    workspace_root 为 None 时, 落到全局兜底目录 ``<global>/projects/__global__/``.
    """
    if workspace_root is None:
        base = paths.projects_dir() / "__global__" / "sessions"
        base.mkdir(parents=True, exist_ok=True)
    else:
        base = paths.project_state_dir(workspace_root) / "sessions"
        base.mkdir(parents=True, exist_ok=True)
    return SessionLog(base / f"{session_id}.jsonl")


def find_session_log_path(session_id: str) -> Path | None:
    """按 session_id 在全局 projects 目录下查找已存在的日志文件."""
    projects = paths.projects_dir()
    if not projects.exists():
        return None
    for p in projects.glob(f"*/sessions/{session_id}.jsonl"):
        return p
    return None
