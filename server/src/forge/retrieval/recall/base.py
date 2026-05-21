"""召回抽象层.

定位:
    - 把 "向量库" / "BM25 索引" 等异构存储, 统一为 "给一个 query, 返回子块命中"
    - 输出统一的 ChildHit DTO, 后续 fusion 层不需要感知召回路差异
    - 一路召回 = 一个 Recall 实例; 多路召回由 retrieval pipeline 编排

扩展点:
    - 加新一路召回 (如稠密+稀疏混合、HyDE) -> 实现 Recall 接口即可
    - VectorRecall 内部 query→embedding 的步骤抽出成可替换组件,
      为未来加 query 改写 / HyDE / 缓存留位
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ChildHit:
    """召回阶段统一的子块命中 DTO.

    score 一律 "越大越相关", 但量纲在不同 recall 路之间不可比
    (向量是 cosine 相似度, BM25 是 IDF 打分). 跨路融合应基于 rank 而非 score.

    rank 是该路内的排名, 1-based. RRF 默认基于 1-based rank.

    source 表明命中路径, 用于:
        - 日志 / 调试
        - fusion 层根据来源选择融合权重 (weighted 策略)
        - 未来可能的多路召回去重/审计
    """

    chunk_id: str
    parent_id: str
    doc_id: str
    score: float
    rank: int
    source: str


class Recall(ABC):
    """单路召回抽象基类."""

    @property
    @abstractmethod
    def name(self) -> str:
        """召回路标识. 用于填充 ChildHit.source 与日志."""

    @abstractmethod
    def search(
        self,
        query: str,
        top_k: int,
        doc_id_filter: list[str] | None = None,
    ) -> list[ChildHit]:
        """召回.

        Args:
            query:          原始查询字符串
            top_k:          返回前 k 条
            doc_id_filter:  可选 doc_id 白名单, 过滤下推到底层存储
                            None  -> 不过滤
                            []    -> 严格返回 [] (空白名单, 安全语义)

        Returns:
            按 score 降序排列的 ChildHit 列表, rank 已填充 (1-based),
            source 已填充为 self.name.

        实现要求:
            - doc_id_filter 必须下推到底层存储, 不做事后过滤
            - 失败抛异常, 不做内部重试 (本地存储失败通常是配置/损坏问题)
        """
