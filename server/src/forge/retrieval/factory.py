"""RetrieverFactory: 按配置装配 ParentChildRetriever.

设计原则:
    - 只装配, 不创建基础设施. parent_store / child_store / bm25_store /
      embedder 由调用方 (main.py / bootstrap) 创建并传入, 工厂负责把它们
      装成 retriever.
    - 装配代码集中一处, 主程序里只调一次 RetrieverFactory.create.

配置访问约定:
    settings 是已加载的 Settings 对象. 工厂只读取 settings.retrieval；
    Reranker 实例由系统模型绑定解析后传入.
"""

from __future__ import annotations

import logging
from typing import Any

from forge.retrieval.stores.bm25.base import BM25Store
from forge.retrieval.stores.vector.base import ChildVectorStore

from .base import RetrievalConfig
from .embedders.base import Embedder
from .fusion.factory import AggregatorFactory, FusionFactory
from .pipeline import ParentChildRetriever
from .recall.bm25_recall import BM25Recall
from .recall.vector_recall import VectorRecall

logger = logging.getLogger(__name__)


class RetrieverFactory:
    """按 settings 组装出 ParentChildRetriever 实例."""

    @staticmethod
    def create(
        settings: Any,
        child_store: ChildVectorStore | None,
        bm25_store: BM25Store,
        embedder: Embedder | None,
        reranker=None,
    ) -> ParentChildRetriever:
        """
        Args:
            settings:    已加载的 Settings 对象. 期望含
                         retrieval / reranker 字段.
            child_store: Chroma 向量库, 由 bootstrap 创建.
            bm25_store:  SQLite FTS5 BM25 索引, 由 bootstrap 创建.
            embedder:    embedding 模型, 由 bootstrap 创建.
            reranker:    可选，预构建的 Reranker 实例。传入后跳过 settings 构建。
        """
        rcfg = settings.retrieval

        # ----- 1. Recall 路 -----
        recall_cfg = rcfg.recall
        vector_recall = None
        if recall_cfg.vector.enabled and child_store is not None and embedder is not None:
            vector_recall = VectorRecall(child_store, embedder)

        bm25_recall = None
        if recall_cfg.bm25.enabled:
            bm25_recall = BM25Recall(bm25_store)

        if vector_recall is None and bm25_recall is None:
            raise ValueError(
                "至少要启用一路 recall: retrieval.recall.vector.enabled 或 "
                "retrieval.recall.bm25.enabled"
            )

        # ----- 2. Fusion -----
        fusion_cfg = rcfg.fusion
        fusion = FusionFactory.create(
            fusion_cfg.strategy,
            {
                "rrf_k": getattr(fusion_cfg, "rrf_k", 60),
                "weights": {
                    "vector": float(getattr(fusion_cfg, "weighted_vector", 0.7)),
                    "bm25": float(getattr(fusion_cfg, "weighted_bm25", 0.3)),
                },
            },
        )

        # ----- 3. Aggregator -----
        aggregator = AggregatorFactory.create(rcfg.aggregation.score_agg)

        # ----- 4. Reranker: 仅使用系统绑定传入的实例 -----
        rerank_enabled = reranker is not None and bool(rcfg.rerank.enabled)
        if not rerank_enabled:
            reranker = None
            logger.info("rerank 流程未绑定模型或已禁用")

        # ----- 5. RetrievalConfig -----
        config = RetrievalConfig(
            top_n_parent=rcfg.top_n_parent,
            top_m_for_rerank=rcfg.top_m_for_rerank,
            vector_top_k=recall_cfg.vector.top_k,
            bm25_top_k=recall_cfg.bm25.top_k,
            rerank_enabled=rerank_enabled,
            vector_enabled=vector_recall is not None,
            bm25_enabled=bm25_recall is not None,
        )

        # ----- 6. HyDE (可选, 仅向量召回存在时有意义) -----
        hyde = None
        hyde_cfg = getattr(rcfg, "hyde", None)
        if hyde_cfg is not None and hyde_cfg.enabled and vector_recall is not None:
            try:
                from forge.llm import get_llm_gateway

                from .query_expansion import HydeGenerator

                hyde = HydeGenerator(
                    get_llm_gateway(settings),
                    max_tokens=hyde_cfg.max_tokens,
                    concat_original=hyde_cfg.concat_original,
                )
                logger.info("HyDE 查询扩展已启用 (max_tokens=%d)", hyde_cfg.max_tokens)
            except Exception as e:  # noqa: BLE001
                logger.warning("HyDE 初始化失败, 已禁用: %s", e)
                hyde = None

        return ParentChildRetriever(
            vector_recall=vector_recall,
            bm25_recall=bm25_recall,
            fusion=fusion,
            aggregator=aggregator,
            reranker=reranker,
            config=config,
            hyde=hyde,
        )
