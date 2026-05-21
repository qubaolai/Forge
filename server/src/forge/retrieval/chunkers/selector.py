"""切分策略选择器.

根据 Element 流的特征自动选合适的 chunker, 让 IngestPipeline 不用关心格式细节.

判断规则 (优先级从高到低):
    1. 出现 SHEET_META 或 ROW (Excel 特征)         → RowBasedChunker
    2. 出现 TITLE (Word/MD 等有层级结构)            → HierarchicalChunker
    3. 其他 (纯文本, 无标题)                        → SlidingWindowChunker

设计考虑:
    - 用扫描 element 类型而不是按文件后缀, 因为 chunker 不应该感知格式.
      这样即使将来 PDF parser 输出 TITLE, 也能自动走层级切分.
    - Excel 优先级最高: SHEET_META/ROW 的存在足以判定为表格型数据,
      即使误带了 TITLE 也应该走行级切分 (sheet 名通常会被解析成 TITLE).
    - 仅 1 个 TITLE 也走 HierarchicalChunker, 由 chunker 内部的动态层级
      选择逻辑决定如何处理 (可能 fallback 到滑窗).
    - 空列表兜底走 SlidingWindowChunker, 不会报错.
"""

from __future__ import annotations

import logging

from forge.core.types import Element, ElementType

from .base import BaseChunker, ChunkConfig
from .strategies import HierarchicalChunker, SlidingWindowChunker

logger = logging.getLogger(__name__)

# Excel 特征类型: 出现任一即判定为行级数据
_EXCEL_TYPES = frozenset({ElementType.SHEET_META, ElementType.ROW})


def select_chunker(
    elements: list[Element],
    config: ChunkConfig | None = None,
) -> BaseChunker:
    """根据 element 特征选择合适的 chunker.

    Args:
        elements: parser 输出的 Element 列表.
        config: 切分参数, 所有策略共用; None 时用默认值.

    Returns:
        已用 config 初始化的 chunker 实例.
    """
    config = config or ChunkConfig()

    # 单次扫描收集特征, 避免多次遍历
    # TODO: 接入 RowBasedChunker 后, 再检测 _EXCEL_TYPES 并走 Excel 路径
    has_title = False
    for el in elements:
        if el.type in _EXCEL_TYPES:
            # 当前未实现 RowBasedChunker, Excel 也走层级或滑窗
            break
        if el.type == ElementType.TITLE:
            has_title = True
            # 不 break, 因为后续可能出现 SHEET_META, 需要让 Excel 优先

    if has_title:
        logger.debug("select_chunker → HierarchicalChunker (检测到 TITLE)")
        return HierarchicalChunker(config)

    logger.debug("select_chunker → SlidingWindowChunker (无层级特征)")
    return SlidingWindowChunker(config)
