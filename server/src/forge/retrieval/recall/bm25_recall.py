"""BM25 召回路: 包装 BM25Store, 把 BM25Hit 转为统一的 ChildHit."""

from __future__ import annotations

import logging

from forge.retrieval.stores.bm25.base import BM25Store

from .base import ChildHit, Recall

logger = logging.getLogger(__name__)


class BM25Recall(Recall):
    """BM25 召回 (子块粒度).

    本类只做 store 与 retrieval 层的边界翻译, 不持有分词器
    (分词器已被 BM25Store 持有, 避免 "入库 tokenizer ≠ 查询 tokenizer").
    """

    def __init__(self, bm25_store: BM25Store):
        self._store = bm25_store

    @property
    def name(self) -> str:
        return "bm25"

    def search(
        self,
        query: str,
        top_k: int,
        doc_id_filter: list[str] | None = None,
    ) -> list[ChildHit]:
        # 安全语义: 空白名单严格返回空
        if doc_id_filter is not None and len(doc_id_filter) == 0:
            return []

        bm25_hits = self._store.search(
            query=query,
            top_k=top_k,
            doc_id_filter=doc_id_filter,
        )

        hits: list[ChildHit] = [
            ChildHit(
                chunk_id=h.chunk_id,
                parent_id=h.parent_id,
                doc_id=h.doc_id,
                score=h.score,
                rank=h.rank,
                source=self.name,
            )
            for h in bm25_hits
        ]
        logger.debug("BM25Recall: query=%r top_k=%d hits=%d", query, top_k, len(hits))
        return hits
