"""ContentStore: 统一的「会话内容真相源」只读 + 带 range 切片抽象.

定位 (与 MessageStore 的分工):
    - ContentStore 不是第二套消息存储, 而是「ref 解析 + 带 line_range 切片」的薄封装,
      复用 ChatMessageRepository 的既有读路径。
    - 真相源全文不迁移: chat 在 chat_messages.content。
    - 让上下文里的引用占位 ([ref:msg:<id>]) 能被工具按需切片回读 (paging)。

ref 格式:
    - chat: "msg:<message_id>"  -> DbMessageContentStore
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class ContentSlice:
    """一次 ContentStore.get 的返回.

    text:           切片后的文本 (line_range 为 None 时 = 全文)。
    total_lines:    原文总行数 (供调用方判断是否需要继续翻页)。
    returned_range: 实际返回的行号区间 (1-based 闭区间); 全文时为 (1, total_lines)。
    truncated:      是否只返回了原文的一部分。
    """

    text: str
    total_lines: int
    returned_range: tuple[int, int]
    truncated: bool


def parse_ref(ref: str) -> tuple[str, str]:
    """解析统一引用. 返回 (kind, id); kind ∈ {"msg", "file"}。

    兼容裸 "[ref:msg:xxx]" 包裹形式与 "msg:xxx" / "file:xxx" 纯形式。
    无法解析时返回 ("", "")。
    """
    s = (ref or "").strip()
    if s.startswith("[ref:") and s.endswith("]"):
        s = s[len("[ref:"): -1]
    elif s.startswith("ref:"):
        s = s[len("ref:"):]
    kind, _, ident = s.partition(":")
    kind = kind.strip()
    ident = ident.strip()
    if kind in ("msg", "file") and ident:
        return kind, ident
    return "", ""


def slice_text(
    content: str, line_range: tuple[int, int] | None
) -> ContentSlice:
    """按 1-based 闭区间行号切片. line_range=None 返回全文.

    越界自动夹紧; start>end 或非法时返回全文。

    注: 用 split("\n") 而非 splitlines(), 与 segmenter / code_skeleton 的行号体系
    保持一致 (二者均按 \n 计行), 避免 digest 锚点 LX-Y 与 read_message 回读区间错位。
    """
    lines = content.split("\n")
    total = len(lines)
    if not line_range:
        return ContentSlice(
            text=content, total_lines=total,
            returned_range=(1, total), truncated=False,
        )
    start, end = line_range
    start = max(1, int(start))
    end = min(total, int(end)) if end else total
    if start > end or total == 0:
        return ContentSlice(
            text=content, total_lines=total,
            returned_range=(1, total), truncated=False,
        )
    chunk = "\n".join(lines[start - 1: end])
    truncated = not (start == 1 and end == total)
    return ContentSlice(
        text=chunk, total_lines=total,
        returned_range=(start, end), truncated=truncated,
    )


class ContentStore(ABC):
    """会话内容真相源的只读切片抽象."""

    @abstractmethod
    async def get(
        self, ref: str, line_range: tuple[int, int] | None = None
    ) -> ContentSlice | None:
        """取 ref 对应的全文 (或按 line_range 切片)。ref 不存在返回 None。"""
        ...

    @abstractmethod
    async def exists(self, ref: str) -> bool:
        ...


class DbMessageContentStore(ContentStore):
    """chat 后端: 真相源 = chat_messages.content (经 ChatMessageRepository)."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] | None = None
    ) -> None:
        self._factory = session_factory

    def _get_factory(self):
        if self._factory is not None:
            return self._factory
        from forge.infrastructure.database.database import get_session_factory
        return get_session_factory()

    async def _load_content(
        self, message_id: str, *, owner_user_id: str | None = None
    ) -> str | None:
        """读消息原文。owner_user_id 非空时校验该消息所属会话归属此用户,

        越权一律当作「未找到」返回 None (不泄露存在性)。
        """
        from forge.infrastructure.database.repositories.chat_message_repo import (
            ChatMessageRepository,
        )
        factory = self._get_factory()
        async with factory() as db:
            repo = ChatMessageRepository(db)
            view = await repo.get_by_id(message_id)
            if view is None:
                return None
            if owner_user_id is not None:
                from forge.infrastructure.database.repositories.chat_session_repo import (
                    ChatSessionRepository,
                )
                session = await ChatSessionRepository(db).get_by_id(view.session_id)
                if session is None or session.user_id != owner_user_id:
                    return None  # 越权: 当作未找到
        return view.content or ""

    async def get(
        self,
        ref: str,
        line_range: tuple[int, int] | None = None,
        *,
        owner_user_id: str | None = None,
    ) -> ContentSlice | None:
        kind, ident = parse_ref(ref)
        if kind != "msg":
            return None
        content = await self._load_content(ident, owner_user_id=owner_user_id)
        if content is None:
            return None
        return slice_text(content, line_range)

    async def exists(self, ref: str, *, owner_user_id: str | None = None) -> bool:
        kind, ident = parse_ref(ref)
        if kind != "msg":
            return False
        return (await self._load_content(ident, owner_user_id=owner_user_id)) is not None


__all__ = [
    "ContentSlice",
    "ContentStore",
    "DbMessageContentStore",
    "parse_ref",
    "slice_text",
]
