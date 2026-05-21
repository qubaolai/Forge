"""共享数据模型包.

对外暴露 Element 和 Chunk 两个核心类型, 所有 processor 和 chunker
模块应从此处导入.
"""

from .chunk import Chunk, ChunkMetadata, ChunkStrategy, ChunkType
from .element import Element, ElementMetadata, ElementType

__all__ = [
    "Element",
    "ElementType",
    "ElementMetadata",
    "Chunk",
    "ChunkType",
    "ChunkStrategy",
    "ChunkMetadata",
]
