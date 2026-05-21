"""BM25 倒排索引存储抽象.

入库阶段方法:
    - add_children:   写入子块到 BM25 索引 (内部分词, 调用方不关心 token)
    - delete_by_doc:  按 doc_id 清理 (UPDATED 路径 + 显式删除)
    - count_by_doc:   调试统计

检索阶段方法:
    - search:         给定 query 与可选 doc_id_filter, 返回 top_k 子块命中

DTO:
    - BM25Hit: 子块粒度命中, 父块聚合是 retrieval 层职责
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from forge.core.types import Chunk


@dataclass
class BM25Hit:
    """BM25 单条命中.

    score 一律是 "越大越相关" (实现层负责把 SQLite FTS5 的负值打分翻转).
    rank 是 1-based, 本路内的排名.
    """

    chunk_id: str
    parent_id: str
    doc_id: str
    score: float
    rank: int
    # TODO: 未来需要按 doc_version 过滤时, 在此扩展 doc_version 字段并贯通 search()


class BM25Store(ABC):
    """BM25 倒排索引抽象基类."""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def add_children(self, children: list[Chunk]) -> None:
        """批量写入子块到 BM25 索引.

        要求:
            - 实现层须保证 chunk_id 幂等 (同 chunk_id 重复写入应覆盖, 不报错)
            - 内部使用 tokenizer 对 chunk.content (含 header_path) 分词
            - 不存原文, 只存分词后的 token 流 + 元数据 ID
        """

    @abstractmethod
    def delete_by_doc(self, doc_id: str) -> int:
        """按 doc_id 删除该文档的所有子块索引.

        Returns:
            被删除的子块数量.
        """

    @abstractmethod
    def count_by_doc(self, doc_id: str) -> int:
        """统计某文档的子块数量, 调试用."""

    @abstractmethod
    def search(
        self,
        query: str,
        top_k: int,
        doc_id_filter: list[str] | None = None,
    ) -> list[BM25Hit]:
        """BM25 检索.

        Args:
            query:          原始查询字符串, 实现层内部分词后查询
            top_k:          返回前 k 条
            doc_id_filter:  可选 doc_id 白名单, 过滤下推到 SQL 内部

        Returns:
            按 score 降序排列的 BM25Hit 列表, rank 已填充 (1-based)
        """
