"""Reranker 输入文档的截断策略抽象.

定位:
    - 当 reranker 上游模型有单文档长度限制时, 由本策略决定如何裁剪文档
    - 接口接收 query, 即使当前 tail 策略用不上, 是为后续策略 (智能截断 /
      关键词截断) 留位, 避免接口反复变更
    - 策略应对批次级监控负责 (如截断率告警), 而非只处理单文档

边界:
    - 不感知具体 reranker provider, 任何按字符数限长的 reranker 都能复用
    - 不做 token 级别截断 (按字符数近似), token-aware 是后续优化点
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TruncationStrategy(ABC):
    """文档截断策略."""

    def __init__(self, config: dict):
        self.config = config

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名, 用于日志."""

    @abstractmethod
    def truncate(
        self,
        query: str,
        documents: list[str],
        max_chars: int,
    ) -> list[str]:
        """对 documents 批量截断.

        Args:
            query:     查询文本, tail 策略不使用; 智能策略可用于定位关键片段
            documents: 待截断文档列表
            max_chars: 单文档最大字符数

        Returns:
            与 documents 等长的列表, 每项长度 <= max_chars
        """
