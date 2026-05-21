"""向量召回路: 包装 ChildVectorStore + Embedder.

扩展点:
    把 "query → embedding" 步骤抽成 _encode_query 方法, 子类或外部装饰
    可替换为: query 改写后 embed / HyDE / multi-vector / embedding 缓存等.
    V1 直接调 embedder.embed_query, 接口先定下来, 不破坏未来扩展.
"""

from __future__ import annotations

import logging

from forge.retrieval.stores.vector.base import ChildVectorStore

from ..embedders.base import Embedder
from .base import ChildHit, Recall

logger = logging.getLogger(__name__)


class VectorRecall(Recall):
    """向量召回 (子块粒度)."""

    def __init__(
        self,
        child_store: ChildVectorStore,
        embedder: Embedder,
    ):
        self._store = child_store
        self._embedder = embedder

    @property
    def name(self) -> str:
        return "vector"

    def search(
        self,
        query: str,
        top_k: int,
        doc_id_filter: list[str] | None = None,
    ) -> list[ChildHit]:
        if not query:
            return []
        if top_k <= 0:
            return []
        # 安全语义: 空白名单严格返回空
        if doc_id_filter is not None and len(doc_id_filter) == 0:
            return []

        query_embedding = self._encode_query(query)

        vec_hits = self._store.search(
            query_embedding=query_embedding,
            top_k=top_k,
            doc_id_filter=doc_id_filter,
        )

        hits: list[ChildHit] = []
        for rank, h in enumerate(vec_hits, start=1):
            hits.append(
                ChildHit(
                    chunk_id=h.chunk_id,
                    parent_id=h.parent_id,
                    doc_id=h.doc_id,
                    score=h.score,
                    rank=rank,
                    source=self.name,
                )
            )

        logger.debug(
            "VectorRecall: query=%r top_k=%d hits=%d",
            query,
            top_k,
            len(hits),
        )
        return hits

    # ------------------------------------------------------------------
    # 扩展点
    # TODO: 多向量召回 (multi-vector / ColBERT 风格) 需要把返回类型改为 list[list[float]]
    # 并扩展 ChildVectorStore.search 接收向量列表; 当前接口为单向量场景定义
    # ------------------------------------------------------------------
    def _encode_query(self, query: str) -> list[float]:
        """把 query 编码为向量.

        默认实现直接调 embedder.embed_query. 子类可重写以实现:
            - query 改写后 embed
            - HyDE: 先用 LLM 生成假想答案, 再 embed 假想答案
            - 多向量召回: 返回 list[list[float]] 时需要扩展接口
            - embedding 缓存: 同样的 query 不重复请求
        """
        return self._embedder.embed_query(query)
