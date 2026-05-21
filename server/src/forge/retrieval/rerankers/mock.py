"""MockReranker: 直通实现.

用途:
    - 单元测试 (不依赖外部模型)
    - 配置 provider=mock 时 pipeline 能跑通, 用于排查问题时隔离 rerank 这一环
    - 性能基线对照 (关闭 rerank 的效果)

行为:
    保持输入顺序, 给递减分数 (1/(i+1)).
    top_n 截断照常生效.
"""

from __future__ import annotations

from .base import Reranker, RerankResult
from .factory import register_reranker


@register_reranker("mock")
class MockReranker(Reranker):
    @property
    def model_name(self) -> str:
        return "mock"

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> list[RerankResult]:
        if not query:
            raise ValueError("query 不能为空")
        if not documents:
            return []

        n = len(documents)
        limit = n if top_n is None else min(top_n, n)
        return [RerankResult(index=i, score=1.0 / (i + 1)) for i in range(limit)]
