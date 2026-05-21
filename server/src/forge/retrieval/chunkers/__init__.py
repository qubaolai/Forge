"""切分层入口.

设计原则:
    - 与文档格式解耦: parser 输出 Element, chunker 只认 Element.
    - 策略可替换: HierarchicalChunker / SlidingWindowChunker 按需选择.
    - 父子结构统一: ID 生成、hash、parent_id 绑定都在基类完成.
"""

from .base import BaseChunker, ChunkConfig, compute_text_hash
from .selector import select_chunker
from .strategies.hierarchical import HierarchicalChunker
from .strategies.sliding_window import SlidingWindowChunker

__all__ = [
    "BaseChunker",
    "ChunkConfig",
    "HierarchicalChunker",
    "SlidingWindowChunker",
    "compute_text_hash",
    "select_chunker",
]
