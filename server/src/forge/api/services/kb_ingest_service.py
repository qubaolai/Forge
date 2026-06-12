"""KB 文档入库服务 (Saga 风格).

定位:
    - 接收已落地的 kb_documents 行 + 本地文件路径
    - 解析 → 切分 → embed → 写多存储 (Chroma + BM25 + MySQL parent chunks)
    - 同步推进 kb_documents.status 状态机: parsing → chunking → embedding → indexed
    - 失败时回滚 status='failed' + 补偿清理库外存储

为什么是 Saga 而不是真原子:
    Chroma / SQLite-FTS5 / MySQL 三处存储无法 2PC, 用"提交点 + 补偿":
    - 提交点: kb_documents.status='indexed' (在 MySQL 单事务内 commit)
    - 提交点之前任何失败 → 补偿清理已写入的库外存储
    - 补偿期间 kb_documents 状态仍是 failed, 检索查不到 (list_indexed 过滤),
      下次重新上传走 ingest 即可

设计取舍 (与旧 IngestService 的区别):
    - 不做 doc_diagnose: 上传 API 负责 content_hash 去重, 此服务只管"我收到
      一个文档, 完整入库一遍"
    - doc_id 由调用方 (上传 API / CLI 脚本) 创建好传入, 这里只填充字段
    - 写入路径不再写老的 documents 表, 全部走 kb_documents + kb_document_chunks

事务边界:
    - 单一外部 session (传入); 流程内部不开新 session, 不嵌套事务
    - 写完 MySQL 由调用方 commit; 服务失败时调用方 rollback
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from forge.core.types import Chunk, ChunkType
from forge.infrastructure.database.orm.kb_document_orm import KbDocumentOrm
from forge.infrastructure.database.orm.knowledge_base_orm import (
    KnowledgeBaseOrm,
)
from forge.infrastructure.database.repositories.kb_document_chunk_repo import (
    KbDocumentChunkRepository,
)

# KbDocumentChunkRepository 不在 S6.5 7 个 Store ABC 内, 暂保留直接 import.
from forge.infrastructure.database.repositories.kb_document_repo import (
    KbDocumentRepository,
)
from forge.infrastructure.database.repositories.knowledge_base_repo import (
    KnowledgeBaseRepository,
)
from forge.retrieval.chunkers import ChunkConfig, select_chunker
from forge.retrieval.parsers.dispatcher import ParserDispatcher
from forge.retrieval.stores.bm25.base import BM25Store
from forge.retrieval.stores.vector.base import ChildVectorStore

logger = logging.getLogger(__name__)


class KbIngestError(Exception):
    """KB 入库失败. 调用方需 rollback session 并触发补偿."""


class KbIngestService:
    """KB 文档入库流水线."""

    def __init__(
        self,
        *,
        bm25_store: BM25Store,
        rag_runtime,
        parser_dispatcher: ParserDispatcher,
        chunk_config: ChunkConfig | None = None,
    ):
        self.bm25_store = bm25_store
        self.rag_runtime = rag_runtime
        self.parser_dispatcher = parser_dispatcher
        self.chunk_config = chunk_config or ChunkConfig()

    # ==================================================================
    # 主入口
    # ==================================================================
    async def ingest(
        self,
        *,
        session: AsyncSession,
        kb: KnowledgeBaseOrm,
        document: KbDocumentOrm,
        file_path: Path,
        update_kb_stats: bool = True,
    ) -> dict:
        """完整入库流水线.

        Args:
            session:  外部传入的 AsyncSession (用于状态机 + 父块写入)
            kb:       已存在的 KB ORM
            document: 已落地的 kb_documents 行 (status 通常为 pending)
            file_path: 本地文件路径 (调用方负责将上传内容落到磁盘)

        Returns:
            {parents, children, status} 摘要

        Raises:
            KbIngestError: 任意阶段失败. 调用方需要 rollback session +
                           处理补偿 (服务内已尝试清理库外存储).
        """
        kb_repo = KnowledgeBaseRepository(session)
        doc_repo = KbDocumentRepository(session)
        chunk_repo = KbDocumentChunkRepository(session)  # 不在 S6.5 Store ABC 内

        embedder = await self.rag_runtime.resolve_embedding()
        child_store = await self.rag_runtime.vector_store_for(embedder)
        doc_id = str(document.id)
        kb_id = str(kb.id)

        # 1. parsing
        await doc_repo.update_status(doc_id, "parsing", progress=10)
        parser = self.parser_dispatcher.get(file_path)
        if parser is None:
            await doc_repo.update_status(
                doc_id,
                "failed",
                message=f"未支持的文件格式: {file_path.suffix}",
            )
            raise KbIngestError(
                f"未支持的文件格式: {file_path.suffix}. "
                f"已支持: {self.parser_dispatcher.supported_extensions()}"
            )

        try:
            elements = parser.parse(file_path)
        except Exception as e:
            await doc_repo.update_status(doc_id, "failed", message=f"解析失败: {e}")
            raise KbIngestError(f"解析 {file_path} 失败: {e}") from e

        if not elements:
            await doc_repo.update_status(doc_id, "failed", message="解析为空, 无可入库内容")
            raise KbIngestError(f"文件 {file_path} 解析结果为空")

        # 2. chunking
        await doc_repo.update_status(doc_id, "chunking", progress=30)
        chunker = select_chunker(elements, self.chunk_config)
        chunks = chunker.chunk(elements, doc_id=doc_id, doc_version="v1")
        # 注入 kb_id (chunker 不感知 KB)
        for ch in chunks:
            ch.kb_id = kb_id
        parents = [c for c in chunks if c.chunk_type == ChunkType.PARENT]
        children = [c for c in chunks if c.chunk_type == ChunkType.CHILD]
        if not parents:
            await doc_repo.update_status(doc_id, "failed", message="切分后无可入库父块")
            raise KbIngestError(f"文件 {file_path} 切分结果为空")

        # 3. embedding
        await doc_repo.update_status(doc_id, "embedding", progress=60)
        embeddings: list[list[float]] = []
        if embedder is not None:
            try:
                embed_texts = [self._build_embed_text(c) for c in children]
                embeddings = embedder.embed_documents(embed_texts) if children else []
            except Exception as e:
                await doc_repo.update_status(doc_id, "failed", message=f"向量化失败: {e}")
                raise KbIngestError(f"embed 失败: {e}") from e

        # 4. Saga 写库外 + MySQL
        try:
            await self._persist(
                document=document,
                parents=parents,
                children=children,
                embeddings=embeddings,
                chunk_repo=chunk_repo,
                child_store=child_store,
            )
        except Exception as e:
            logger.exception("入库失败, 触发补偿清理: doc=%s", document.id)
            self._compensate(doc_id, child_store)
            await doc_repo.update_status(doc_id, "failed", message=f"入库失败: {e}")
            raise KbIngestError(f"入库失败: {e}") from e

        # 5. 终态
        await doc_repo.update_status(
            doc_id,
            "indexed",
            progress=100,
            chunk_count=len(parents),
            mark_indexed=True,
        )
        document.embedding_model_id = (
            int(cast(Any, embedder)._forge_model_id) if embedder is not None else None
        )
        document.vector_index_status = "ready" if embedder is not None else "stale"
        document.vector_index_error = None
        document.vector_indexed_at = document.indexed_at if embedder is not None else None
        if update_kb_stats:
            await kb_repo.update_stats(
                kb_id,
                chunk_count_delta=len(parents),
            )

        logger.info(
            "KB 入库完成: kb=%s doc=%s parents=%d children=%d",
            kb.id,
            document.id,
            len(parents),
            len(children),
        )
        return {
            "document_id": document.id,
            "parents": len(parents),
            "children": len(children),
            "status": "indexed",
        }

    async def rebuild_vector_index(
        self,
        *,
        document: KbDocumentOrm,
        file_path: Path,
        before_vector_write: Callable[[], Awaitable[None]] | None = None,
    ) -> dict:
        """仅重建当前绑定模型的向量索引，不修改 BM25、父块或主文档状态。"""
        embedder = await self.rag_runtime.resolve_embedding()
        child_store = await self.rag_runtime.vector_store_for(embedder)
        if embedder is None or child_store is None:
            raise KbIngestError("尚未配置可用的 RAG Embedding 模型")

        parser = self.parser_dispatcher.get(file_path)
        if parser is None:
            raise KbIngestError(f"未支持的文件格式: {file_path.suffix}")
        try:
            elements = parser.parse(file_path)
        except Exception as exc:
            raise KbIngestError(f"解析 {file_path} 失败: {exc}") from exc
        if not elements:
            raise KbIngestError(f"文件 {file_path} 解析结果为空")

        chunker = select_chunker(elements, self.chunk_config)
        doc_id = str(document.id)
        chunks = chunker.chunk(elements, doc_id=doc_id, doc_version="v1")
        children = [c for c in chunks if c.chunk_type == ChunkType.CHILD]
        for child in children:
            child.kb_id = str(document.kb_id)
        try:
            embeddings = embedder.embed_documents(
                [self._build_embed_text(child) for child in children]
            ) if children else []
        except Exception as exc:
            raise KbIngestError(f"embed 失败: {exc}") from exc

        if before_vector_write is not None:
            await before_vector_write()
        child_store.delete_by_doc(doc_id)
        if children:
            child_store.add_children(children, embeddings)

        document.embedding_model_id = int(cast(Any, embedder)._forge_model_id)
        document.vector_index_status = "ready"
        document.vector_index_error = None
        document.vector_indexed_at = datetime.utcnow()
        return {"document_id": document.id, "children": len(children), "status": "ready"}

    # ==================================================================
    # 主动删除 (kb_documents 已存在, 清掉所有存储)
    # ==================================================================
    async def delete_document(
        self,
        *,
        session: AsyncSession,
        document: KbDocumentOrm,
    ) -> None:
        """删除文档的所有索引数据 (库外 + parent chunks).

        kb_documents 本身的删除由调用方 (CRUD service) 控制, 这里只清索引.
        """
        # 先清库外 (顺序无关, 失败也尽量继续)
        try:
            embedder = await self.rag_runtime.resolve_embedding()
            child_store = await self.rag_runtime.vector_store_for(embedder)
            if child_store is not None:
                child_store.delete_by_doc(str(document.id))
        except Exception:  # noqa: BLE001
            logger.exception("删除向量库子块失败: doc=%s", document.id)
        try:
            self.bm25_store.delete_by_doc(str(document.id))
        except Exception:  # noqa: BLE001
            logger.exception("删除 BM25 子块失败: doc=%s", document.id)
        # 再清 MySQL 父块 (CASCADE 也能带走, 但显式删避免依赖外键)
        chunk_repo = KbDocumentChunkRepository(session)
        await chunk_repo.delete_by_document(document.id)

    # ==================================================================
    # 内部实现
    # ==================================================================
    async def _persist(
        self,
        *,
        document: KbDocumentOrm,
        parents: list[Chunk],
        children: list[Chunk],
        embeddings: list[list[float]],
        chunk_repo: KbDocumentChunkRepository,
        child_store: ChildVectorStore | None,
    ) -> None:
        """落库阶段.

        清理顺序: child_store → bm25_store → MySQL parent
        写入顺序: child_store → bm25_store → MySQL (parent chunks)
        提交点:   MySQL 由调用方 commit (kb_documents.update_status 也在
                  同一 session, 调用方 commit 时统一生效)
        """
        # 4.1 清理库外 (重入时兜底)
        doc_id = str(document.id)
        if child_store is not None:
            child_store.delete_by_doc(doc_id)
        self.bm25_store.delete_by_doc(doc_id)
        # 4.2 清父块 (重入时兜底)
        await chunk_repo.delete_by_document(document.id)

        # 4.3 写库外
        if children:
            if child_store is not None:
                child_store.add_children(children, embeddings)
            self.bm25_store.add_children(children)

        # 4.4 写 MySQL 父块 (commit 由外层控制)
        parent_dicts = [self._chunk_to_parent_dict(p, str(document.kb_id)) for p in parents]
        await chunk_repo.save_many(parent_dicts)

    def _compensate(self, document_id: str, child_store: ChildVectorStore | None) -> None:
        """异常发生后清掉库外存储 (best-effort, 每步独立 try)."""
        try:
            if child_store is not None:
                child_store.delete_by_doc(document_id)
        except Exception:  # noqa: BLE001
            logger.exception("补偿: 清向量库失败")
        try:
            self.bm25_store.delete_by_doc(document_id)
        except Exception:  # noqa: BLE001
            logger.exception("补偿: 清 BM25 索引失败")

    @staticmethod
    def _build_embed_text(chunk: Chunk) -> str:
        """子块 embedding 文本: 拼 header_path 让标题语义参与检索."""
        if chunk.header_path:
            return f"{chunk.header_path}\n\n{chunk.content}"
        return chunk.content

    @staticmethod
    def _chunk_to_parent_dict(chunk: Chunk, kb_id: str) -> dict:
        """Chunk → kb_document_chunks.save_many 入参."""
        from dataclasses import asdict, is_dataclass

        meta = chunk.metadata
        if is_dataclass(meta):
            meta_dict = asdict(meta)
        elif isinstance(meta, dict):
            meta_dict = meta
        else:
            meta_dict = {}

        # ChunkMetadata.extra 才是真正的 KV 容器 (page / section 等)
        extra = meta_dict.get("extra") if isinstance(meta_dict, dict) else None
        if not isinstance(extra, dict):
            extra = {}
        # 把其他可序列化字段也带进 extra (strategy / element_count 等),
        # 方便前端引用面板按需展示
        for k, v in (meta_dict or {}).items():
            if k == "extra":
                continue
            if v is None:
                continue
            extra.setdefault(k, v)

        return {
            "id": chunk.chunk_id,
            "document_id": chunk.doc_id,
            "kb_id": kb_id,
            "content": chunk.content,
            "header_path": chunk.header_path or "",
            "chunk_hash": chunk.chunk_hash or "",
            "source_type": chunk.source_type or "text",
            "extra": extra,
            "seq": 0,
        }
