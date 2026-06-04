"""Ollama 本地 Embedding 实现 (OpenAI 兼容 /v1/embeddings).

模型: 由 models.name 指定 (如 nomic-embed-text / bge-m3 等已 pull 的向量模型)
SDK: openai (复用项目已有依赖, 指向 Ollama 的 OpenAI 兼容端点)

config 字段 (本类 __init__ 接受, 来自数据库 embedding_model_configs):
    model:       必填, 来自 models.name
    dimension:   必填, 来自 embedding_model_configs.dimension
    batch_size:  必填
    supported_dimensions: 必填
    max_batch_size: 必填
    max_retries: 默认 3
    retry_backoff: 重试退避基数 (秒), 默认 1.0, 指数增长
    base_url:    可选, 默认 http://localhost:11434/v1; 远程 Ollama 写在
                 embedding 配置的 provider_options.base_url (resolver 只透传
                 provider_options, 不透传 providers.base_url)
    api_key:     可选, 默认占位 "ollama"; Ollama 本地无鉴权, 但 openai SDK
                 要求非空 key
    timeout:     可选, 默认 60.0 (本地推理可能较慢)

实现要点:
    - 实例级 client, 不污染全局状态
    - 超过 batch_size 自动分批
    - 网络 / 超时 / 5xx / 429 指数退避重试; 4xx 视为不可重试直接抛
    - 不传 dimensions 参数: Ollama 向量模型维度固定, 不支持裁剪;
      返回维度与配置不一致直接 fail (提示检查 model / dimension)
    - 本地推理无计费, 不做 cost 上报
"""

from __future__ import annotations

import logging
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from .base import Embedder
from .factory import register_embedder

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_PLACEHOLDER_KEY = "ollama"  # Ollama 本地无鉴权, openai SDK 需非空 key


@register_embedder("ollama")
class OllamaEmbedder(Embedder):
    """Ollama 本地文本向量化 (OpenAI 兼容)."""

    def __init__(self, config: dict):
        super().__init__(config)
        model = config.get("model")
        if not model:
            raise ValueError("OllamaEmbedder 需要 config['model']")
        dimension = config.get("dimension")
        if dimension is None:
            raise ValueError("OllamaEmbedder 需要 config['dimension']")
        batch_size = config.get("batch_size")
        if batch_size is None:
            raise ValueError("OllamaEmbedder 需要 config['batch_size']")
        supported_dimensions = config.get("supported_dimensions")
        if not supported_dimensions:
            raise ValueError("OllamaEmbedder 需要 config['supported_dimensions']")
        max_batch_size = config.get("max_batch_size")
        if max_batch_size is None:
            raise ValueError("OllamaEmbedder 需要 config['max_batch_size']")
        if int(dimension) <= 0:
            raise ValueError("OllamaEmbedder config['dimension'] 必须大于 0")
        if int(batch_size) <= 0:
            raise ValueError("OllamaEmbedder config['batch_size'] 必须大于 0")
        dimensions = [int(value) for value in supported_dimensions]
        if int(dimension) not in dimensions:
            raise ValueError(
                f"OllamaEmbedder dimension={dimension} 不在 supported_dimensions={dimensions} 中"
            )
        if int(batch_size) > int(max_batch_size):
            raise ValueError(
                f"OllamaEmbedder batch_size={batch_size} 超过 max_batch_size={max_batch_size}"
            )

        self._model: str = str(model)
        self._dim: int = int(dimension)
        self._batch_size: int = int(batch_size)
        self._max_retries: int = config.get("max_retries", 3)
        self._retry_backoff: float = config.get("retry_backoff", 1.0)

        base_url = config.get("base_url") or _DEFAULT_BASE_URL
        api_key = config.get("api_key") or _PLACEHOLDER_KEY
        timeout = config.get("timeout", 60.0)
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

        logger.info(
            "Ollama Embedder ready: model=%s, dim=%d, batch=%d, base_url=%s",
            self._model,
            self._dim,
            self._batch_size,
            base_url,
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
        return self._embed_with_batches(texts)

    def embed_query(self, text: str) -> list[float]:
        result = self._embed_with_batches([text])
        return result[0]

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _embed_with_batches(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        total = len(texts)
        for start in range(0, total, self._batch_size):
            end = min(start + self._batch_size, total)
            batch = texts[start:end]
            results.extend(self._embed_one_batch(batch))
        return results

    def _embed_one_batch(self, batch: list[str]) -> list[list[float]]:
        last_err: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.embeddings.create(model=self._model, input=batch)
            except APIStatusError as e:
                if not self._should_retry(e.status_code):
                    raise RuntimeError(
                        f"Ollama embedding 失败 (不可重试): model={self._model}, "
                        f"status={e.status_code}, message={e}"
                    ) from e
                last_err = e
                logger.warning(
                    "Ollama HTTP 错误可重试 (attempt %d/%d): model=%s status=%s",
                    attempt + 1, self._max_retries + 1, self._model, e.status_code,
                )
                self._sleep_backoff(attempt)
                continue
            except (APITimeoutError, APIConnectionError) as e:
                last_err = e
                logger.warning(
                    "Ollama 连接/超时异常 (attempt %d/%d): model=%s err=%s",
                    attempt + 1, self._max_retries + 1, self._model, e,
                )
                self._sleep_backoff(attempt)
                continue

            return self._parse_embeddings(resp, expected=len(batch))

        raise RuntimeError(
            f"Ollama embedding 重试 {self._max_retries} 次后仍失败: "
            f"model={self._model}, error={last_err}"
        )

    @staticmethod
    def _should_retry(status_code: int | None) -> bool:
        if status_code is None:
            return True
        return status_code == 429 or status_code >= 500

    def _sleep_backoff(self, attempt: int) -> None:
        if attempt >= self._max_retries:
            return
        delay = self._retry_backoff * (2**attempt)
        time.sleep(delay)

    def _parse_embeddings(self, resp, expected: int) -> list[list[float]]:
        items = list(resp.data or [])
        if len(items) != expected:
            raise RuntimeError(
                f"Ollama 返回向量数量不一致: model={self._model}, 期望 {expected}, 实际 {len(items)}"
            )
        sorted_items = sorted(items, key=lambda x: x.index)
        vectors = [list(item.embedding) for item in sorted_items]
        if vectors and len(vectors[0]) != self._dim:
            raise RuntimeError(
                f"Ollama 返回维度 {len(vectors[0])} 与配置 {self._dim} 不一致, "
                f"检查 model={self._model} 和 dimension 配置"
            )
        return vectors
