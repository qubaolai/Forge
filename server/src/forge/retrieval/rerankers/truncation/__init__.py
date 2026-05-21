"""Reranker 输入文档截断策略."""

from .base import TruncationStrategy
from .factory import TruncationFactory, register_truncation

__all__ = [
    "TruncationStrategy",
    "TruncationFactory",
    "register_truncation",
]
