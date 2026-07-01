"""检索查询归一化 (query-side).

只做无歧义、对简体/半角语料净收益的归一化, 低风险:
    - 去首尾空白 + 折叠内部连续空白
    - Unicode NFKC (全角 ASCII → 半角、兼容字形折叠, 如 "ＡＰＩ"→"API")
    - 繁 → 简 (仅当 opencc / zhconv 可用时启用; 否则跳过, 不引入硬依赖)

作用范围: 归一化后的 query 同时喂给向量召回、BM25 召回与 rerank,
保证三处用同一份 query (由 pipeline._retrieve 在入口统一处理)。

注意: 当前仅在查询侧归一化。入库侧对称归一化 (让语料也走同一规则)
是后续项, 需要重建索引; 在简体/半角为主的语料上, 仅查询侧归一化即为净收益。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")

# 繁简转换器懒加载缓存: None=未探测, False=不可用, Callable=可用
_zh_converter: Callable[[str], str] | bool | None = None


def normalize_query(text: str) -> str:
    """归一化检索 query. None/空白 → 空串."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _to_simplified(normalized)
    return _WS_RE.sub(" ", normalized).strip()


def _to_simplified(text: str) -> str:
    global _zh_converter
    if _zh_converter is None:
        _zh_converter = _load_zh_converter()
    if _zh_converter is False:
        return text
    try:
        return _zh_converter(text)  # type: ignore[operator]
    except Exception:  # noqa: BLE001
        return text


def _load_zh_converter() -> Callable[[str], str] | bool:
    """优先 opencc, 退 zhconv; 都没有则禁用繁简转换."""
    try:
        from opencc import OpenCC

        cc = OpenCC("t2s")
        return lambda s: cc.convert(s)
    except Exception:  # noqa: BLE001
        pass
    try:
        import zhconv

        return lambda s: zhconv.convert(s, "zh-hans")
    except Exception:  # noqa: BLE001
        pass
    logger.info("未安装繁简转换库 (opencc/zhconv), query 归一化跳过繁→简")
    return False
