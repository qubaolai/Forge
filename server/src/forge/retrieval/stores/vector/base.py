"""子块向量存储抽象.

入库阶段方法:
    - add_children:   写入子块向量
    - delete_by_doc:  按文档清理 (UPDATED 路径用)
    - count_by_doc:   调试统计

检索阶段方法:
    - search: 给定 query 向量, 返回 top_k 子块命中
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from forge.core.types import Chunk


@dataclass
class VectorHit:
    """向量库单条命中.

    score 一律 "越大越相关" (实现层负责把 cosine distance 翻转为相似度).
    保持 store 层私有 DTO, 不引用 retrieval 层的 ChildHit, 避免反向依赖.
    """

    chunk_id: str
    parent_id: str
    doc_id: str
    score: float


class ChildVectorStore(ABC):
    """子块向量存储抽象基类."""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def add_children(
        self,
        children: list[Chunk],
        embeddings: list[list[float]],
    ) -> None:
        """批量写入子块向量.

        Args:
            children:   子块 Chunk 列表.
            embeddings: 与 children 等长的向量列表, 由 Embedder 预先算好.

        要求:
            - 实现层须保证幂等 (同 chunk_id 重复写入应覆盖, 不报错)
            - metadata 至少包含 parent_id / doc_id / doc_version
              (后续 retriever 和 delete_by_doc 依赖)
        """

    @abstractmethod
    def delete_by_doc(self, doc_id: str) -> int:
        """按 doc_id 删除该文档的所有子块向量.

        Returns:
            被删除的子块数量.
        """

    @abstractmethod
    def count_by_doc(self, doc_id: str) -> int:
        """统计某文档的子块数量, 调试用."""

    @abstractmethod
    def search(
        self,
        query_embedding: list[float],
        top_k: int,
        doc_id_filter: list[str] | None = None,
    ) -> list[VectorHit]:
        """向量检索.

        Args:
            query_embedding: 已 embed 的 query 向量, 由调用方 (VectorRecall) 算好
            top_k:           返回前 k 条
            doc_id_filter:   可选 doc_id 白名单, 过滤下推到向量库 where
                             None  -> 不过滤
                             []    -> 严格返回 [] (空白名单, 不视为不过滤)

        Returns:
            按 score 降序排列的 VectorHit 列表.
        """
