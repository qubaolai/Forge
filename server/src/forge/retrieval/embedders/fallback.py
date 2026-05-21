"""Embedder Fallback 链: 主 provider 失败时按顺序切换备用 provider.

设计要点:
    - 维度一致性是硬约束. 所有 fallback 必须与主 provider 同维度, 否则
      切换后向量库 (Chroma collection) 维度不匹配, 读写都会炸.
      __init__ 时强校验, 不一致直接 raise.
    - 失败语义跟 LLMFallbackChain 对齐: SDK 抛异常视为可重试, 顺序尝试
      链中下一个; 全部失败时抛最后一次的异常.
    - check_budget: 仅在 embed_query (查询路径) 调用前做一次; embed_documents
      (入库批量) 走后台任务, 超额不阻断, 由 cost flush 上层告警.
    - 计费 record: 由各 concrete embedder 实现内部完成, 本类不重复 record.
"""

from __future__ import annotations

import logging

from forge.llm.cost_tracker import LLMBudgetExceeded, get_cost_tracker

from .base import Embedder

logger = logging.getLogger(__name__)


class EmbedderFallbackChain(Embedder):
    """主 + 备用 Embedder 列表, 按顺序调用直到成功.

    所有成员对外暴露统一 Embedder 接口, ParentChildRetriever / VectorRecall
    都不感知是否在链里.
    """

    def __init__(self, primary: Embedder, fallbacks: list[Embedder] | None = None) -> None:
        if not isinstance(primary, Embedder):
            raise TypeError(f"primary 必须是 Embedder 子类, 收到 {type(primary).__name__}")

        fallbacks = list(fallbacks or [])
        # 维度一致性硬校验
        for fb in fallbacks:
            if fb.dimension != primary.dimension:
                raise ValueError(
                    f"EmbedderFallbackChain 维度不一致: 主 {primary.model_name} "
                    f"dim={primary.dimension}, 备用 {fb.model_name} dim={fb.dimension}. "
                    f"向量库无法跨维度读写, 配置不可用."
                )

        # 直接初始化父类的 config 字段 (供外部按需读取); 链本身没有自己的 config
        super().__init__({"primary": primary.model_name})

        self._primary = primary
        self._fallbacks = fallbacks
        self._chain: list[Embedder] = [primary, *fallbacks]
        self._cost = get_cost_tracker()

        if fallbacks:
            logger.info(
                "EmbedderFallbackChain 就绪: primary=%s fallbacks=%s dim=%d",
                primary.model_name,
                [fb.model_name for fb in fallbacks],
                primary.dimension,
            )
        else:
            logger.info(
                "EmbedderFallbackChain 单链 (无 fallback): provider=%s dim=%d",
                primary.model_name,
                primary.dimension,
            )

    # ------------------------------------------------------------------
    # Embedder 接口
    # ------------------------------------------------------------------
    @property
    def dimension(self) -> int:
        return self._primary.dimension

    @property
    def model_name(self) -> str:
        return self._primary.model_name

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """文档入库批量 embed. 走 fallback 但不做预算检查."""
        return self._call_chain(lambda emb: emb.embed_documents(texts))

    def embed_query(self, text: str) -> list[float]:
        """查询 embed. 调用前 check_budget, 超额抛 LLMBudgetExceeded."""
        # 预算检查只在查询路径做; ingest 路径见 embed_documents
        self._cost.check_budget()
        return self._call_chain(lambda emb: emb.embed_query(text))

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _call_chain(self, fn):
        """顺序尝试链中每个 embedder, 全失败时抛最后一次异常."""
        last_err: Exception | None = None
        for idx, emb in enumerate(self._chain):
            try:
                return fn(emb)
            except LLMBudgetExceeded:
                # 预算异常不该被 fallback 救场, 换 provider 也是花用户的钱
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                if idx + 1 < len(self._chain):
                    next_name = self._chain[idx + 1].model_name
                    logger.warning(
                        "Embedder %s 调用失败, 切换到下一个 %s: %s",
                        emb.model_name,
                        next_name,
                        e,
                    )
                else:
                    logger.exception("Embedder 链全部失败 (last=%s)", emb.model_name)
        assert last_err is not None
        raise last_err
