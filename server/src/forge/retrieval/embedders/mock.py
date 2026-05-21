"""Mock Embedder, 用于第一阶段跑通入库链路."""

from __future__ import annotations

import hashlib
import struct

from .base import Embedder
from .factory import register_embedder


@register_embedder("mock")
class MockEmbedder(Embedder):
    """确定性零成本 Embedder.

    - 不做语义建模, 切勿在生产中使用
    - 同一文本多次 embed 结果一致, 便于测试
    - 向量经 L2 归一化, 与 cosine 距离配套

    config 接受字段:
        dimension: 向量维度, 默认 768
        model_name: 模型名, 默认 "mock"
    """

    def __init__(self, config: dict):
        self._dim = config.get("dimension", 768)
        self._name = config.get("model_name", "mock")

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._name

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_to_vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._hash_to_vector(text)

    def _hash_to_vector(self, text: str) -> list[float]:
        """SHA256 派生确定性伪随机向量, L2 归一化后返回."""
        raw = b""
        seed = text.encode("utf-8")
        while len(raw) < self._dim * 4:
            seed = hashlib.sha256(seed).digest()
            raw += seed
        floats = list(struct.unpack(f"{self._dim}f", raw[: self._dim * 4]))

        norm = sum(x * x for x in floats) ** 0.5
        if norm == 0:
            return floats
        return [x / norm for x in floats]
