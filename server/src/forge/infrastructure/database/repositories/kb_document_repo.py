"""KB 文档仓储 (kb_documents 表).

主要服务两类调用:
    - knowledge_search 工具: 把 kb_ids 解析为可检索的 doc_ids (status='indexed')
    - 上传/入库流程: 创建文档、更新状态机 (pending → indexed/failed)、删除
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
from forge.infrastructure.database.repositories.base import BaseRepository
from forge.infrastructure.storage.data_protocols import KbDocumentStore

logger = logging.getLogger(__name__)


def _to_int(value: str | int | None) -> int | None:
    """对外 ID (str(雪花)) → BIGINT; 非法/空返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class KbDocumentRepository(BaseRepository, KbDocumentStore):
    """KB 文档 CRUD + 状态机."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    async def create(self, doc: KbDocumentOrm) -> KbDocumentOrm:
        """新建 KB 文档. 不 commit."""
        self.session.add(doc)
        await self.session.flush()
        return doc

    async def update_status(
        self,
        doc_id: str,
        status: str,
        *,
        message: str | None = None,
        progress: int | None = None,
        chunk_count: int | None = None,
        mark_indexed: bool = False,
    ) -> KbDocumentOrm | None:
        """更新文档状态. 不 commit.

        Args:
            status:        pending / parsing / chunking / embedding / indexed / failed
            message:       状态详情 / 失败原因
            progress:      0-100 进度
            chunk_count:   入库分块数 (终态时填)
            mark_indexed:  设为 True 时同时更新 indexed_at = now()
        """
        doc = await self.session.get(KbDocumentOrm, _to_int(doc_id))
        if doc is None:
            return None
        doc.status = status
        if message is not None:
            doc.status_message = message
        if progress is not None:
            doc.progress = max(0, min(100, progress))
        if chunk_count is not None:
            doc.chunk_count = chunk_count
        if mark_indexed:
            doc.indexed_at = datetime.utcnow()
        await self.session.flush()
        return doc

    async def delete(self, doc: KbDocumentOrm) -> None:
        """硬删除文档 (CASCADE 带走 kb_document_chunks). 不 commit."""
        await self.session.delete(doc)
        await self.session.flush()

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    async def get(self, doc_id: str) -> KbDocumentOrm | None:
        return await self.session.get(KbDocumentOrm, _to_int(doc_id))

    async def get_in_kb(self, doc_id: str, kb_id: str) -> KbDocumentOrm | None:
        """获取并校验文档归属 KB (用于鉴权前置)."""
        doc = await self.session.get(KbDocumentOrm, _to_int(doc_id))
        if doc is None or doc.kb_id != _to_int(kb_id):
            return None
        return doc

    async def list_by_kb(
        self,
        kb_id: str,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[KbDocumentOrm], int]:
        """分页列出某 KB 下的文档. 返回 (items, total)."""
        kid = _to_int(kb_id)
        base = select(KbDocumentOrm).where(KbDocumentOrm.kb_id == kid)
        count_base = (
            select(func.count()).select_from(KbDocumentOrm).where(KbDocumentOrm.kb_id == kid)
        )
        if status is not None:
            base = base.where(KbDocumentOrm.status == status)
            count_base = count_base.where(KbDocumentOrm.status == status)

        total_res = await self.session.execute(count_base)
        total = int(total_res.scalar_one())

        stmt = (
            base.order_by(KbDocumentOrm.created_at.desc())
            .offset(max(0, (page - 1) * page_size))
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def list_indexed_doc_ids(self, kb_ids: list[str]) -> list[str]:
        """返回指定 KB 列表下所有已索引 (status='indexed') 文档的 ID.

        Args:
            kb_ids: KB ID 列表; 空列表返回 []

        Returns:
            doc_id 列表. 未索引 / 失败 / 处理中状态的文档不返回 (检索查不到).
        """
        if not kb_ids:
            return []
        ids = [i for i in (_to_int(k) for k in kb_ids) if i is not None]
        if not ids:
            return []
        stmt = select(KbDocumentOrm.id).where(
            KbDocumentOrm.kb_id.in_(ids),
            KbDocumentOrm.status == "indexed",
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def list_vector_ready_doc_ids(self, kb_ids: list[str], model_id: str) -> list[str]:
        if not kb_ids:
            return []
        ids = [i for i in (_to_int(k) for k in kb_ids) if i is not None]
        mid = _to_int(model_id)
        if not ids or mid is None:
            return []
        stmt = select(KbDocumentOrm.id).where(
            KbDocumentOrm.kb_id.in_(ids),
            KbDocumentOrm.status == "indexed",
            KbDocumentOrm.vector_index_status == "ready",
            KbDocumentOrm.embedding_model_id == mid,
        )
        result = await self.session.execute(stmt)
        return [str(row[0]) for row in result.all()]

    async def find_by_content_hash(
        self,
        kb_id: str,
        content_hash: str,
    ) -> KbDocumentOrm | None:
        """同 KB 内按内容哈希查重 (用于上传去重)."""
        stmt = (
            select(KbDocumentOrm)
            .where(
                KbDocumentOrm.kb_id == _to_int(kb_id),
                KbDocumentOrm.content_hash == content_hash,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
