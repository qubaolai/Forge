"""知识库 (KB) 仓储.

权限模型:
    - private:   仅 owner 可见
    - workspace: owner + collaborators 可见 (协作者结构见 KnowledgeBaseOrm)
    - public:    全用户可见

knowledge_search 工具用 find_accessible_by_names 同时做"按名查"+"权限过滤":
    工具拿到 LLM 传来的 kb_names + 当前请求 user_id, 这里负责过滤,
    工具不需要自己重复写鉴权.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.knowledge_base_orm import (
    KnowledgeBaseOrm,
)
from forge.infrastructure.database.repositories.base import BaseRepository
from forge.infrastructure.storage.data_protocols import KnowledgeBaseStore

logger = logging.getLogger(__name__)


def _to_int(value: str | int | None) -> int | None:
    """对外 ID (str(雪花)) → BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class KnowledgeBaseRepository(BaseRepository, KnowledgeBaseStore):
    """知识库 CRUD + 按名/权限查询."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------
    # 创建 / 读 / 更新 / 删除
    # ------------------------------------------------------------------
    async def create(self, kb: KnowledgeBaseOrm) -> KnowledgeBaseOrm:
        """新建 KB. 不 commit, 由调用方控制事务."""
        self.session.add(kb)
        await self.session.flush()
        return kb

    async def get(self, kb_id: str) -> KnowledgeBaseOrm | None:
        return await self.session.get(KnowledgeBaseOrm, _to_int(kb_id))

    async def get_owned(self, kb_id: str, user_id: str) -> KnowledgeBaseOrm | None:
        """单机模式: 仅校验是否存在."""
        _ = user_id
        return await self.session.get(KnowledgeBaseOrm, _to_int(kb_id))

    async def list_for_user(self, user_id: str) -> list[KnowledgeBaseOrm]:
        """单机模式: 列出全部 KB."""
        _ = user_id
        stmt = select(KnowledgeBaseOrm).order_by(KnowledgeBaseOrm.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, kb: KnowledgeBaseOrm) -> None:
        """删除 KB (硬删除, CASCADE 会带走 kb_documents 与 kb_document_chunks)."""
        await self.session.delete(kb)
        await self.session.flush()

    async def update_stats(
        self,
        kb_id: str,
        *,
        document_count_delta: int = 0,
        chunk_count_delta: int = 0,
        size_bytes_delta: int = 0,
    ) -> None:
        """增量更新 KB 统计字段. 不 commit."""
        kb = await self.session.get(KnowledgeBaseOrm, _to_int(kb_id))
        if kb is None:
            return
        kb.document_count = max(0, (kb.document_count or 0) + document_count_delta)
        kb.chunk_count = max(0, (kb.chunk_count or 0) + chunk_count_delta)
        kb.size_bytes = max(0, (kb.size_bytes or 0) + size_bytes_delta)
        await self.session.flush()

    # ------------------------------------------------------------------
    # knowledge_search 工具使用
    # ------------------------------------------------------------------
    async def find_accessible_by_names(
        self,
        names: list[str],
        user_id: str,
    ) -> list[KnowledgeBaseOrm]:
        """按名称查可访问的 KB. 权限规则:

            - public                  → 任何人可读
            - workspace / private 中 owner_id == user_id → 可读
            - workspace 中 user_id 在 collaborators 里     → 可读 (TODO: 暂未实现)

        TODO: collaborators 是 JSON 列表, 真实工作区协作模型上线后再补
              MySQL JSON_CONTAINS 路径过滤. 当前 workspace 仅 owner 可读.

        Args:
            names:    KB 显示名列表; 空列表返回 []
            user_id:  当前请求用户 ID; 空字符串视为匿名, 仅能看 public KB

        Returns:
            命中且通过权限校验的 KB ORM 列表. 顺序不保证, 调用方按需排序.
        """
        if not names:
            return []

        _ = user_id

        stmt = select(KnowledgeBaseOrm).where(KnowledgeBaseOrm.name.in_(names))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
