"""向量量化与相似度的共享工具.

被 context_mgmt (消息向量缓存) 与 memory (用户事实召回) 共同引用,
放在 utils 层避免 memory -> context_mgmt 的反向依赖.

存储瘦身约定:
    - 向量以 int8 对称量化字节存 DB (LargeBinary), 约 18x 压缩;
    - 余弦相似度对正标量 scale 不变, 故无需回存 scale,
      读时直接用 int8 还原值算 cosine.
"""

from __future__ import annotations

import math
from array import array


def quantize_int8(vec: list[float]) -> bytes:
    """对称量化为 int8 字节: scale = max(|v|)/127; q = round(v/scale), clip 到 [-127,127].

    余弦相似度对正标量 scale 不变, 故无需回存 scale。全零向量 (peak=0) 退化为全零字节。
    """
    if not vec:
        return b""
    peak = max(abs(x) for x in vec)
    if peak == 0.0:
        return bytes(len(vec))
    scale = peak / 127.0
    return array(
        "b", (max(-127, min(127, round(x / scale))) for x in vec)
    ).tobytes()


def dequantize_int8(blob: bytes | None) -> list[float]:
    """int8 字节还原为 list[float] (按量化值原样, 不乘 scale —— cosine 不受影响)。"""
    if not blob:
        return []
    return [float(x) for x in array("b", blob)]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """纯 Python 余弦相似度. 任一向量为零向量时返回 0.0。"""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
