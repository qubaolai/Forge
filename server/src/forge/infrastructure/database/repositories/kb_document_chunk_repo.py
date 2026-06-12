"""KB 文档分块仓储 (kb_document_chunks 表).

定位:
    父子检索流水线中"父块原文存储"的入口. 子块只活在 Chroma / BM25, 没有
    MySQL 存储; 父块在这里, 检索命中后通过 chunk_id 回查全文.

接口约定:
    - 写方法 (save_many / delete_by_document) 不 commit, 由外层事务控制
    - 读方法返回纯 dict, 不暴露 ORM 给 retriever (保持检索层无 SQLAlchemy 依赖)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.orm.kb_document_chunk_orm import (
    KbDocumentChunkOrm,
)
from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
from forge.infrastructure.database.orm.knowledge_base_orm import (
    KnowledgeBaseOrm,
)
from forge.infrastructure.database.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


def _to_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_extra(extra: Any) -> dict:
    """允许传入 dict / JSON-string / None, 统一成 dict."""
    if extra is None:
        return {}
    if isinstance(extra, dict):
        return extra
    if isinstance(extra, str):
        try:
            parsed = json.loads(extra)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


class KbDocumentChunkRepository(BaseRepository):
    """KB 文档父块存储."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ==================================================================
    # 写
    # ==================================================================
    async def save_many(self, parents: list[dict]) -> None:
        """批量保存父块. 已存在则覆盖 (upsert by id).

        Args:
            parents: 字典列表, 每项必须包含:
                id (chunk_id), document_id, kb_id, content, header_path,
                chunk_hash, source_type, extra (dict, optional),
                seq (int, optional), token_count (int, optional).
        """
        if not parents:
            return

        # SQLite / MySQL 分别走各自原生 upsert 语法, 其他方言退化到 merge.
        rows = []
        for p in parents:
            rows.append(
                {
                    "id": p["id"],
                    "document_id": _to_int(p["document_id"]),
                    "kb_id": _to_int(p["kb_id"]),
                    "seq": int(p.get("seq", 0)),
                    "content": p["content"],
                    "header_path": p.get("header_path", "") or "",
                    "chunk_hash": p.get("chunk_hash", "") or "",
                    "source_type": p.get("source_type", "text"),
                    "extra": _normalize_extra(p.get("extra")),
                    "token_count": int(p.get("token_count", 0)),
                }
            )

        bind = self.session.get_bind()
        dialect = bind.dialect.name if bind is not None else ""
        if dialect == "sqlite":
            sqlite_stmt = sqlite_insert(KbDocumentChunkOrm).values(rows)
            sqlite_stmt = sqlite_stmt.on_conflict_do_update(
                index_elements=[KbDocumentChunkOrm.id],
                set_={
                    "document_id": sqlite_stmt.excluded.document_id,
                    "kb_id": sqlite_stmt.excluded.kb_id,
                    "seq": sqlite_stmt.excluded.seq,
                    "content": sqlite_stmt.excluded.content,
                    "header_path": sqlite_stmt.excluded.header_path,
                    "chunk_hash": sqlite_stmt.excluded.chunk_hash,
                    "source_type": sqlite_stmt.excluded.source_type,
                    "extra": sqlite_stmt.excluded.extra,
                    "token_count": sqlite_stmt.excluded.token_count,
                },
            )
            await self.session.execute(sqlite_stmt)
            return

        if dialect in {"mysql", "mariadb"}:
            mysql_stmt = mysql_insert(KbDocumentChunkOrm).values(rows)
            mysql_stmt = mysql_stmt.on_duplicate_key_update(
                document_id=mysql_stmt.inserted.document_id,
                kb_id=mysql_stmt.inserted.kb_id,
                seq=mysql_stmt.inserted.seq,
                content=mysql_stmt.inserted.content,
                header_path=mysql_stmt.inserted.header_path,
                chunk_hash=mysql_stmt.inserted.chunk_hash,
                source_type=mysql_stmt.inserted.source_type,
                extra=mysql_stmt.inserted.extra,
                token_count=mysql_stmt.inserted.token_count,
            )
            await self.session.execute(mysql_stmt)
            return

        for row in rows:
            await self.session.merge(KbDocumentChunkOrm(**row))

    async def delete_by_document(self, document_id: str | int) -> int:
        """按 document_id 批量删除该文档所有父块. 返回删除行数."""
        stmt = delete(KbDocumentChunkOrm).where(KbDocumentChunkOrm.document_id == _to_int(document_id))
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)

    # ==================================================================
    # 读
    # ==================================================================
    async def count_by_document(self, document_id: str | int) -> int:
        stmt = (
            select(func.count())
            .select_from(KbDocumentChunkOrm)
            .where(KbDocumentChunkOrm.document_id == _to_int(document_id))
        )
        res = await self.session.execute(stmt)
        return int(res.scalar_one())

    async def get_many_enriched(self, chunk_ids: list[str]) -> dict[str, dict]:
        """按 chunk_id 批量查父块, 一次性 JOIN kb_documents + knowledge_bases
        把引用元信息 (document_name / kb_name / source_url) 也带回来.

        Returns:
            {chunk_id: {
                chunk_id, document_id, kb_id, content, header_path,
                source_type, extra (dict), token_count, seq,
                document_name, source_url, kb_name,
            }}
        """
        if not chunk_ids:
            return {}

        stmt = (
            select(
                KbDocumentChunkOrm.id,
                KbDocumentChunkOrm.document_id,
                KbDocumentChunkOrm.kb_id,
                KbDocumentChunkOrm.content,
                KbDocumentChunkOrm.header_path,
                KbDocumentChunkOrm.source_type,
                KbDocumentChunkOrm.extra,
                KbDocumentChunkOrm.token_count,
                KbDocumentChunkOrm.seq,
                KbDocumentOrm.name.label("document_name"),
                KbDocumentOrm.source_url,
                KnowledgeBaseOrm.name.label("kb_name"),
            )
            .join(KbDocumentOrm, KbDocumentChunkOrm.document_id == KbDocumentOrm.id)
            .join(KnowledgeBaseOrm, KbDocumentChunkOrm.kb_id == KnowledgeBaseOrm.id)
            .where(KbDocumentChunkOrm.id.in_(chunk_ids))
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        out: dict[str, dict] = {}
        for row in rows:
            extra = _normalize_extra(row.extra)
            out[row.id] = {
                "chunk_id": row.id,
                "document_id": row.document_id,
                "kb_id": row.kb_id,
                "content": row.content,
                "header_path": row.header_path or "",
                "source_type": row.source_type or "text",
                "extra": extra,
                "token_count": row.token_count or 0,
                "seq": row.seq,
                "document_name": row.document_name or "",
                "source_url": row.source_url,
                "kb_name": row.kb_name or "",
            }
        return out
