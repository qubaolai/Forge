"""扩展位 1 示例: 改写 _encode_query 实现 query 改写 + embedding 缓存.

本文件是教学样本, 不参与生产装配:
    - 演示 VectorRecall 的扩展位如何复用, 而不需要改 base / store / pipeline
    - 演示两种典型扩展叠加在同一子类里 (改写 + 缓存)
    - 用 MockQueryRewriter 替代真实 LLM, 让示例零依赖可跑

真实场景下:
    - QueryRewriter 应该是真实 LLM 调用 (qwen / gpt) 或一个领域改写规则集
    - 缓存可以换成 LRU / TTL 缓存, 持久化到 Redis 等
    - 两种扩展也可以拆成两个独立子类, 然后通过装饰器模式或多重继承组合
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .vector_recall import VectorRecall

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 改写器协议: 真实场景这里换成 LLM 客户端
# ----------------------------------------------------------------------
class QueryRewriter(ABC):
    """Query 改写器协议. 真实实现可以是 LLM / 规则引擎."""

    @abstractmethod
    def rewrite(self, query: str) -> str:
        """把原 query 改写为更适合检索的形式."""
        ...


class MockQueryRewriter(QueryRewriter):
    """Mock 改写器:
    - 给 query 加 "解释一下" 前缀, 模拟把口语化的疑问句改成陈述句
    - 真实 HyDE 应该是用 LLM 生成假想答案, 这里只是示意改写"形态"
    - 记录调用次数, 方便测试断言
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def rewrite(self, query: str) -> str:
        self.calls.append(query)
        # 简单形态改写: 疑问 → 陈述, 加领域词
        rewritten = f"详细解释: {query}"
        logger.debug("MockQueryRewriter: %r -> %r", query, rewritten)
        return rewritten


# ----------------------------------------------------------------------
# 扩展子类
# ----------------------------------------------------------------------
class MockExtendedVectorRecall(VectorRecall):
    """演示扩展位 1: query 改写 + embedding 缓存.

    流程:
        query → rewriter.rewrite → cache 命中? → embedder.embed_query → 返回向量

    扩展只覆盖 _encode_query, 其他逻辑 (rank / source 标记 / filter 透传 /
    空 query 短路) 完全继承父类, 零重复.

    Args:
        child_store, embedder: 同 VectorRecall
        rewriter:              改写器实例, 提供 .rewrite(str) -> str
        cache_size:            最大缓存条目数, 0 表示不缓存
    """

    def __init__(
        self,
        child_store,
        embedder,
        rewriter: QueryRewriter,
        cache_size: int = 128,
    ):
        super().__init__(child_store, embedder)
        self._rewriter = rewriter
        self._cache_size = max(0, cache_size)
        # 用 dict 维护插入顺序, py3.7+ 字典有序, 简易 LRU 不需要 OrderedDict
        self._cache: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # 唯一覆盖点
    # ------------------------------------------------------------------
    def _encode_query(self, query: str) -> list[float]:
        # 1. 改写
        rewritten = self._rewriter.rewrite(query)

        # 2. 缓存查 (key 是改写后的文本, 不是原 query)
        if self._cache_size > 0 and rewritten in self._cache:
            logger.debug("VectorRecall 缓存命中: %r", rewritten)
            return self._cache[rewritten]

        # 3. 实际 embedding
        vec = self._embedder.embed_query(rewritten)

        # 4. 写缓存 (简易 LRU: 满了淘汰最早一个)
        if self._cache_size > 0:
            if len(self._cache) >= self._cache_size:
                oldest = next(iter(self._cache))
                self._cache.pop(oldest)
            self._cache[rewritten] = vec

        return vec

    # ------------------------------------------------------------------
    # 调试辅助
    # ------------------------------------------------------------------
    def cache_stats(self) -> dict:
        """返回当前缓存状态, 用于调试和测试."""
        return {
            "size": len(self._cache),
            "max": self._cache_size,
            "keys": list(self._cache.keys()),
        }
