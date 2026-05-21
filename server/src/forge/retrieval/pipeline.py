"""ParentChildRetriever: 主检索流水线.

流程:
    query
      ├─ run_recalls          (V1 串行, 单路失败降级)
      │     ├─ vector_recall  (可选)
      │     └─ bm25_recall    (可选)
      │
      ▼
    fusion.fuse                 (子块层融合)
      │
      ▼
    aggregator.aggregate        (按 parent_id 聚合到父块)
      │
      ▼
    截 top_m  (仅 rerank 启用时)
      │
      ▼
    parent_store.get_many       (回查父块原文)
      │
      ▼
    reranker.rerank             (失败降级用 fusion_score)
      │
      ▼
    截 top_n_parent
      │
      ▼
    list[RetrievedParent]

容错:
    - 单路 recall 失败 -> ERROR 日志, 该路返回空, 继续走另一路
    - 全部 recall 失败 -> 返回 []
    - rerank 失败       -> WARNING 日志, 降级用 fusion_score, final_score == fusion_score
    - parent_id 在 parent_store 找不到 -> WARNING 跳过

性能:
    各阶段耗时记 INFO 日志, 后续可改造为 metric 上报点.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from forge.infrastructure.database.repositories.kb_document_chunk_repo import (
    KbDocumentChunkRepository,
)

from .base import RetrievalConfig, RetrievedParent
from .fusion.base import (
    AggregatedParent,
    Aggregator,
    FusedHit,
    Fusion,
)
from .recall.base import ChildHit, Recall
from .rerankers.base import Reranker, RerankError

logger = logging.getLogger(__name__)


class ParentChildRetriever:
    """父子块层次化检索主流水线."""

    def __init__(
        self,
        vector_recall: Recall | None,
        bm25_recall: Recall | None,
        fusion: Fusion,
        aggregator: Aggregator,
        reranker: Reranker | None,
        config: RetrievalConfig,
    ):
        if vector_recall is None and bm25_recall is None:
            raise ValueError(
                "ParentChildRetriever 至少需要一路 recall "
                "(vector_recall 与 bm25_recall 不能都为 None)"
            )
        self._vector_recall = vector_recall
        self._bm25_recall = bm25_recall
        self._fusion = fusion
        self._aggregator = aggregator
        self._reranker = reranker
        self._config = config

        logger.info(
            "ParentChildRetriever 就绪: vector=%s bm25=%s fusion=%s aggregator=%s "
            "reranker=%s rerank_enabled=%s",
            "on" if vector_recall else "off",
            "on" if bm25_recall else "off",
            fusion.name,
            aggregator.name,
            reranker.model_name if reranker else "none",
            config.rerank_enabled,
        )

    # ==================================================================
    # 主入口
    # ==================================================================
    async def retrieve(
        self,
        query: str,
        session: AsyncSession,
        doc_id_filter: list[str] | None = None,
        top_n: int | None = None,
    ) -> list[RetrievedParent]:
        """检索.

        Args:
            query:          查询文本
            doc_id_filter:  可选 doc_id 白名单, 透传到两路 recall.
                            None  -> 不过滤
                            []    -> 严格返回 [] (空白名单, 安全语义)
            top_n:          覆盖配置 top_n_parent. None 用配置值.

        Returns:
            按 final_score 降序排列的 RetrievedParent 列表, len <= top_n.
        """
        if not query:
            logger.debug("retrieve: 空 query, 直接返回 []")
            return []

        # 安全语义: 空白名单
        if doc_id_filter is not None and len(doc_id_filter) == 0:
            logger.debug("retrieve: 空白名单, 直接返回 []")
            return []

        effective_top_n = top_n if top_n is not None else self._config.top_n_parent
        if effective_top_n <= 0:
            return []

        t0 = time.perf_counter()

        # 1. 多路召回
        hits_per_source = self._run_recalls(query, doc_id_filter)
        if not any(hits_per_source.values()):
            logger.info("retrieve: 全部 recall 路无命中, 返回 []")
            return []
        t1 = time.perf_counter()

        # 2. 融合
        fused: list[FusedHit] = self._fusion.fuse(hits_per_source)
        if not fused:
            logger.info("retrieve: fusion 后为空")
            return []
        t2 = time.perf_counter()

        # 3. 父块聚合
        aggregated: list[AggregatedParent] = self._aggregator.aggregate(fused)
        if not aggregated:
            logger.info("retrieve: aggregate 后为空")
            return []
        t3 = time.perf_counter()

        # 4. 截 top_m (仅 rerank 启用时), 回查父块原文
        rerank_active = self._is_rerank_active()
        if rerank_active:
            candidates = aggregated[: self._config.top_m_for_rerank]
        else:
            # rerank 不启用时, 不需要 top_m 截断, 直接取 top_n
            candidates = aggregated[:effective_top_n]

        parent_dict = await self._fetch_parents([p.parent_id for p in candidates], session)
        # 过滤掉在 parent_store 找不到的 (数据不一致兜底)
        valid_candidates: list[AggregatedParent] = []
        for p in candidates:
            if p.parent_id in parent_dict:
                valid_candidates.append(p)
            else:
                logger.warning(
                    "retrieve: parent_id=%s 在 parent_store 找不到, 跳过 "
                    "(数据不一致, 检查入库链路)",
                    p.parent_id,
                )
        if not valid_candidates:
            logger.warning("retrieve: 全部候选父块在 parent_store 都找不到, 返回 []")
            return []
        t4 = time.perf_counter()

        # 5. Rerank (可选, 失败降级)
        if rerank_active:
            final = self._do_rerank(
                query,
                valid_candidates,
                parent_dict,
                effective_top_n,
            )
        else:
            final = self._build_results_no_rerank(
                valid_candidates[:effective_top_n],
                parent_dict,
            )
        t5 = time.perf_counter()

        logger.info(
            "retrieve 完成: query_len=%d hits=%s fused=%d aggregated=%d "
            "candidates=%d final=%d "
            "[recall=%.0fms fusion=%.0fms agg=%.0fms fetch=%.0fms rerank=%.0fms]",
            len(query),
            {s: len(h) for s, h in hits_per_source.items()},
            len(fused),
            len(aggregated),
            len(valid_candidates),
            len(final),
            (t1 - t0) * 1000,
            (t2 - t1) * 1000,
            (t3 - t2) * 1000,
            (t4 - t3) * 1000,
            (t5 - t4) * 1000,
        )
        return final

    # ==================================================================
    # 内部: 多路召回
    # ==================================================================
    def _run_recalls(
        self,
        query: str,
        doc_id_filter: list[str] | None,
    ) -> dict[str, list[ChildHit]]:
        """串行调两路 recall, 单路失败 ERROR 日志 + 该路返回空.

        TODO: 后续可改为 ThreadPoolExecutor 并行, 接口不变.
              并行需要确认 Chroma client 与 SQLite connection 的线程安全.
        """
        result: dict[str, list[ChildHit]] = {}

        if self._vector_recall is not None:
            result[self._vector_recall.name] = self._safe_recall(
                self._vector_recall,
                query,
                self._config.vector_top_k,
                doc_id_filter,
            )
        if self._bm25_recall is not None:
            result[self._bm25_recall.name] = self._safe_recall(
                self._bm25_recall,
                query,
                self._config.bm25_top_k,
                doc_id_filter,
            )
        return result

    @staticmethod
    def _safe_recall(
        recall: Recall,
        query: str,
        top_k: int,
        doc_id_filter: list[str] | None,
    ) -> list[ChildHit]:
        try:
            return recall.search(query, top_k, doc_id_filter)
        except Exception:
            logger.exception(
                "recall 路 %r 失败, 该路降级为空; 检索流程继续",
                recall.name,
            )
            return []

    # ==================================================================
    # 内部: 父块原文回查
    # ==================================================================
    async def _fetch_parents(self, parent_ids: list[str], session: AsyncSession) -> dict[str, dict]:
        """开短事务回查父块原文, 并一次性 JOIN 引用元信息.

        返回 dict 已带 document_name / kb_name / source_url, 供 _make_retrieved
        组装成 RetrievedParent. retriever 不持有长生命周期 store.
        """
        if not parent_ids:
            return {}
        return await KbDocumentChunkRepository(session).get_many_enriched(parent_ids)

    # ==================================================================
    # 内部: rerank 启用与否
    # ==================================================================
    def _is_rerank_active(self) -> bool:
        """rerank 实际跑不跑: 既要实例存在, 又要配置开关打开."""
        return self._reranker is not None and self._config.rerank_enabled

    # ==================================================================
    # 内部: rerank 流程
    # ==================================================================
    def _do_rerank(
        self,
        query: str,
        candidates: list[AggregatedParent],
        parent_dict: dict[str, dict],
        top_n: int,
    ) -> list[RetrievedParent]:
        """对 candidates 跑 rerank. 失败降级用 fusion_score."""
        assert self._reranker is not None  # _is_rerank_active 已校验

        # 构造 rerank 输入文本: header_path + content, 与入库 embedding 对齐
        documents = [self._build_rerank_text(parent_dict[p.parent_id]) for p in candidates]

        try:
            results = self._reranker.rerank(query, documents, top_n=top_n)
        except RerankError as e:
            logger.warning(
                "rerank 失败, 降级使用 fusion_score: %s",
                e,
            )
            # 降级: 直接按 fusion_score 取前 top_n (candidates 已按 fusion_score 降序)
            return self._build_results_no_rerank(candidates[:top_n], parent_dict)

        # 按 rerank 结果回贴, results 已按 rerank_score 降序, len <= top_n
        final: list[RetrievedParent] = []
        for r in results:
            parent = candidates[r.index]
            row = parent_dict[parent.parent_id]
            final.append(
                self._make_retrieved(
                    parent,
                    row,
                    rerank_score=r.score,
                )
            )
        return final

    def _build_results_no_rerank(
        self,
        candidates: list[AggregatedParent],
        parent_dict: dict[str, dict],
    ) -> list[RetrievedParent]:
        """rerank 不启用 / 失败降级 路径: 用 fusion_score 排序."""
        return [
            self._make_retrieved(p, parent_dict[p.parent_id], rerank_score=None) for p in candidates
        ]

    # ==================================================================
    # 内部: 文本构造与 DTO 组装
    # ==================================================================
    @staticmethod
    def _build_rerank_text(parent_row: dict) -> str:
        """rerank 输入文本: header_path + content, 与入库 embedding 一致.

        超长会被 reranker 内部的 TruncationStrategy 处理, 这里不截断.
        """
        header = parent_row.get("header_path") or ""
        content = parent_row.get("content") or ""
        return f"{header}\n\n{content}" if header else content

    @staticmethod
    def _make_retrieved(
        parent: AggregatedParent,
        parent_row: dict,
        rerank_score: float | None,
    ) -> RetrievedParent:
        final_score = rerank_score if rerank_score is not None else parent.fusion_score
        extra = parent_row.get("extra") or {}
        page = extra.get("page") if isinstance(extra, dict) else None
        if isinstance(page, str):
            try:
                page = int(page)
            except ValueError:
                page = None
        return RetrievedParent(
            chunk_id=parent_row["chunk_id"],
            document_id=parent_row.get("document_id", ""),
            kb_id=parent_row.get("kb_id", ""),
            document_name=parent_row.get("document_name", "") or "",
            kb_name=parent_row.get("kb_name", "") or "",
            source_url=parent_row.get("source_url"),
            page=page if isinstance(page, int) else None,
            content=parent_row["content"],
            header_path=parent_row.get("header_path", "") or "",
            source_type=parent_row.get("source_type", "") or "",
            final_score=final_score,
            fusion_score=parent.fusion_score,
            rerank_score=rerank_score,
            hit_child_count=parent.hit_child_count,
            hit_chunk_ids=list(parent.hit_chunk_ids),
            metadata=dict(extra) if isinstance(extra, dict) else {},
        )
