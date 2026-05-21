"""Embedding 抽象协议.

约定:
    - embed_documents 与 embed_query 分离, 因为多数现代 embedding 模型
      (BGE / E5 / OpenAI text-embedding-3) 对查询和文档使用不同的 prefix
      或归一化策略, 接口分开避免实现层踩坑.
    - dimension 属性是必需的, 向量库初始化要用.
    - 实现层应保证向量已经做过归一化 (cosine 检索友好), 或在 docstring
      明确未归一化, 让上层决定是否后处理.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Embedder(ABC):
    """Embedding 抽象基类."""

    def __init__(self, config: dict):
        self.config = config

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度, 不同模型不同."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型标识, 用于日志和向量库 collection 命名隔离."""

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量 embed 文档. 入库时使用.

        Returns:
            与输入等长的向量列表, 每个向量为 list[float].
        """

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """单条 embed 查询. 检索时使用. 入库阶段不调用, 但接口先定义."""
