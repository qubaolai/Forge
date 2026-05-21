"""RetrieverFactory: 按配置装配 ParentChildRetriever.

设计原则:
    - 只装配, 不创建基础设施. parent_store / child_store / bm25_store /
      embedder 由调用方 (main.py / bootstrap) 创建并传入, 工厂负责把它们
      装成 retriever.
    - 装配代码集中一处, 主程序里只调一次 RetrieverFactory.create.

配置访问约定:
    settings 是已加载的 Settings 对象. 工厂通过属性访问: settings.retrieval,
    settings.reranker 等. 字段名约定见 sys_config.yaml 重构方案.
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
from .rerankers.factory import build_reranker_from_settings

logger = logging.getLogger(__name__)


class RetrieverFactory:
    """按 settings 组装出 ParentChildRetriever 实例."""

    @staticmethod
    def create(
        settings: Any,
        child_store: ChildVectorStore,
        bm25_store: BM25Store,
        embedder: Embedder,
    ) -> ParentChildRetriever:
        """
        Args:
            settings:    已加载的 Settings 对象. 期望含
                         retrieval / reranker 字段.
            db:          MySQL Database 句柄, retriever 内部按需开 UoW
                         读父块原文.
            child_store: Chroma 向量库, 由 bootstrap 创建.
            bm25_store:  SQLite FTS5 BM25 索引, 由 bootstrap 创建.
            embedder:    embedding 模型, 由 bootstrap 创建.
        """
        rcfg = settings.retrieval

        # ----- 1. Recall 路 -----
        recall_cfg = rcfg.recall
        vector_recall = None
        if recall_cfg.vector.enabled:
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
                "weights": dict(getattr(fusion_cfg, "weights", {}) or {}),
            },
        )

        # ----- 3. Aggregator -----
        aggregator = AggregatorFactory.create(rcfg.aggregation.score_agg)

        # ----- 4. Reranker (按需, 走模型网关池化) -----
        reranker = None
        rerank_enabled = bool(rcfg.rerank.enabled)
        if rerank_enabled:
            reranker = build_reranker_from_settings(settings)
        else:
            logger.info("rerank 流程已禁用 (retrieval.rerank.enabled=false)")

        # ----- 5. RetrievalConfig -----
        config = RetrievalConfig(
            top_n_parent=rcfg.top_n_parent,
            top_m_for_rerank=rcfg.top_m_for_rerank,
            vector_top_k=recall_cfg.vector.top_k,
            bm25_top_k=recall_cfg.bm25.top_k,
            rerank_enabled=rerank_enabled,
        )

        return ParentChildRetriever(
            vector_recall=vector_recall,
            bm25_recall=bm25_recall,
            fusion=fusion,
            aggregator=aggregator,
            reranker=reranker,
            config=config,
        )
