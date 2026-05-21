"""Reranker 抽象协议.

定位:
    - 无状态的纯文本打分排序器, 不感知 RAG 项目的 Chunk 类型
    - 输入是 query + 文档字符串列表, 返回 (index, score) 排序结果
    - 不感知流程开关 (retrieval.rerank.enabled), 那是 retrieval 层的事

错误语义:
    - 失败一律抛 RerankError, 由调用方决定降级策略
    - 实现层可在内部重试瞬时错误 (网络抖动 / 5xx), 但用尽后必须抛
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class RerankError(Exception):
    """Rerank 失败. 包装底层 SDK 异常, 供 retrieval 层统一 catch."""


@dataclass
class RerankResult:
    """Rerank 单条结果.

    index 指向输入 documents 列表的原始位置, 调用方按 index 把分数
    回贴到自己的候选对象上. score 一律越大越相关.
    """

    index: int
    score: float


class Reranker(ABC):
    """Reranker 抽象基类."""

    def __init__(self, config: dict):
        self.config = config

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型标识, 用于日志."""

    @abstractmethod
    def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> list[RerankResult]:
        """对 documents 重排.

        Args:
            query:     查询文本, 不能为空 (空 query 是调用方 bug)
            documents: 候选文档字符串列表 (通常是父块 header_path + content)
            top_n:     只返回前 n 条; None 表示返回全部

        Returns:
            按 score 降序排列的 RerankResult 列表.
            len(返回) <= len(documents), 按 top_n 截断.
            documents 为空时返回 [].

        Raises:
            ValueError:   query 为空
            RerankError:  打分失败 (含重试耗尽), 由调用方决定降级
        """
