"""千问 (DashScope) Embedding 实现.

模型: 由 models.name 指定, 必须匹配 DashScope TextEmbedding 端点
SDK: dashscope (官方)

config 字段 (本类 __init__ 接受, 来自数据库 embedding_model_configs):
    api_key:     必填
    model:       必填, 来自 models.name
    dimension:   必填, 来自 embedding_model_configs.dimension
    batch_size:  必填, 来自 embedding_model_configs.batch_size
    supported_dimensions: 必填, 来自 embedding_model_configs.supported_dimensions
    max_batch_size: 必填, 来自 embedding_model_configs.max_batch_size
    max_retries: 默认 3
    retry_backoff: 重试退避基数 (秒), 默认 1.0, 指数增长

实现要点:
    - api_key 实例级持有, 调用时显式传入, 不写 dashscope.api_key = ...
      (避免多 provider/多 key 共存时互相覆盖)
    - 文档与查询分别走不同的 text_type ("document" / "query")
    - 超过 batch_size 自动分批
    - 网络/限流错误指数退避重试
"""

from __future__ import annotations

import logging
import time
from http import HTTPStatus
from typing import Literal

from dashscope import TextEmbedding

from forge.llm.cost_tracker import get_cost_tracker

from .base import Embedder
from .factory import register_embedder

logger = logging.getLogger(__name__)

_PROVIDER_NAME = "dashscope"


@register_embedder("dashscope")
class DashScopeEmbedder(Embedder):
    """阿里百炼 (DashScope) 文本向量化."""

    def __init__(self, config: dict):
        api_key = config.get("api_key")
        if not api_key:
            raise ValueError("DashScopeEmbedder 需要 config['api_key']")
        model = config.get("model")
        if not model:
            raise ValueError("DashScopeEmbedder 需要 config['model']")
        dimension = config.get("dimension")
        if dimension is None:
            raise ValueError("DashScopeEmbedder 需要 config['dimension']")
        batch_size = config.get("batch_size")
        if batch_size is None:
            raise ValueError("DashScopeEmbedder 需要 config['batch_size']")
        supported_dimensions = config.get("supported_dimensions")
        if not supported_dimensions:
            raise ValueError("DashScopeEmbedder 需要 config['supported_dimensions']")
        max_batch_size = config.get("max_batch_size")
        if max_batch_size is None:
            raise ValueError("DashScopeEmbedder 需要 config['max_batch_size']")
        if int(dimension) <= 0:
            raise ValueError("DashScopeEmbedder config['dimension'] 必须大于 0")
        if int(batch_size) <= 0:
            raise ValueError("DashScopeEmbedder config['batch_size'] 必须大于 0")
        dimensions = [int(value) for value in supported_dimensions]
        if int(dimension) not in dimensions:
            raise ValueError(
                f"DashScopeEmbedder dimension={dimension} 不在 supported_dimensions={dimensions} 中"
            )
        if int(batch_size) > int(max_batch_size):
            raise ValueError(
                f"DashScopeEmbedder batch_size={batch_size} 超过 max_batch_size={max_batch_size}"
            )

        # 实例级持有, 不再写 dashscope.api_key = api_key
        self._api_key: str = api_key
        self._model: str = str(model)
        self._dim: int = int(dimension)
        self._batch_size: int = int(batch_size)
        self._max_retries: int = config.get("max_retries", 3)
        self._retry_backoff: float = config.get("retry_backoff", 1.0)

        logger.info(
            "DashScope Embedder ready: model=%s, dim=%d, batch=%d",
            self._model,
            self._dim,
            self._batch_size,
        )

    # ------------------------------------------------------------------
    # Embedder 接口
    # ------------------------------------------------------------------
    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed_with_batches(texts, text_type="document")

    def embed_query(self, text: str) -> list[float]:
        result = self._embed_with_batches([text], text_type="query")
        return result[0]

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _embed_with_batches(
        self,
        texts: list[str],
        text_type: Literal["document", "query"],
    ) -> list[list[float]]:
        results: list[list[float]] = []
        total = len(texts)

        for start in range(0, total, self._batch_size):
            end = min(start + self._batch_size, total)
            batch = texts[start:end]
            batch_vectors = self._embed_one_batch(batch, text_type)
            results.extend(batch_vectors)

        return results

    def _embed_one_batch(
        self,
        batch: list[str],
        text_type: Literal["document", "query"],
    ) -> list[list[float]]:
        last_err: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                resp = TextEmbedding.call(
                    api_key=self._api_key,  # 实例级 key, 不污染 SDK 全局
                    model=self._model,
                    input=batch,
                    text_type=text_type,
                    dimension=self._dim,
                )
            except Exception as e:
                last_err = e
                logger.warning(
                    "DashScope 调用异常 (attempt %d/%d): %s",
                    attempt + 1,
                    self._max_retries + 1,
                    e,
                )
                self._sleep_backoff(attempt)
                continue

            if resp.status_code == HTTPStatus.OK:
                vectors = self._parse_embeddings(resp, expected=len(batch))
                self._record_cost(resp)
                return vectors

            if self._should_retry(resp.status_code):
                last_err = RuntimeError(
                    f"DashScope HTTP {resp.status_code}: code={resp.code}, message={resp.message}"
                )
                logger.warning(
                    "DashScope HTTP 错误可重试 (attempt %d/%d): %s",
                    attempt + 1,
                    self._max_retries + 1,
                    last_err,
                )
                self._sleep_backoff(attempt)
                continue

            raise RuntimeError(
                f"DashScope embedding 失败 (不可重试): model={self._model}, "
                f"text_type={text_type}, "
                f"status={resp.status_code}, code={resp.code}, message={resp.message}"
            )

        raise RuntimeError(
            f"DashScope embedding 重试 {self._max_retries} 次后仍失败: "
            f"model={self._model}, text_type={text_type}, error={last_err}"
        )

    @staticmethod
    def _should_retry(status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    def _sleep_backoff(self, attempt: int) -> None:
        if attempt >= self._max_retries:
            return
        delay = self._retry_backoff * (2**attempt)
        time.sleep(delay)

    def _record_cost(self, resp) -> None:
        """从 DashScope 响应里抓 usage.total_tokens 上报 CostTracker.

        失败容错: 任何字段缺失 / 类型错误都吞掉, 计费不能阻塞 embedding.
        user_id 走 ContextVar (current_user_id), 无 user 上下文 → 匿名.
        """
        try:
            usage = getattr(resp, "usage", None) or {}
            if hasattr(usage, "get"):
                total = usage.get("total_tokens") or usage.get("input_tokens") or 0
            else:
                total = getattr(usage, "total_tokens", 0) or getattr(usage, "input_tokens", 0) or 0
            if not total:
                return
            get_cost_tracker().record(
                provider=_PROVIDER_NAME,
                model=self._model,
                usage={"prompt_tokens": int(total), "completion_tokens": 0},
            )
        except Exception:  # noqa: BLE001
            logger.debug("DashScope embedder 计费上报失败 (已忽略)", exc_info=True)

    def _parse_embeddings(self, resp, expected: int) -> list[list[float]]:
        items = (
            resp.output.get("embeddings")
            if hasattr(resp.output, "get")
            else resp.output["embeddings"]
        )

        if len(items) != expected:
            raise RuntimeError(f"DashScope 返回向量数量不一致: 期望 {expected}, 实际 {len(items)}")

        sorted_items = sorted(items, key=lambda x: x["text_index"])
        vectors = [item["embedding"] for item in sorted_items]

        if vectors and len(vectors[0]) != self._dim:
            raise RuntimeError(
                f"DashScope 返回维度 {len(vectors[0])} 与配置 {self._dim} 不一致, "
                f"检查 model 和 dimension 配置"
            )

        return vectors
