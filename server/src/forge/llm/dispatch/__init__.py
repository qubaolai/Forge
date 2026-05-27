"""LLM 调度层: 链遍历 + 构链.

LLMDispatcher 是 LLMFallbackChain 的重命名后继: 职责更聚焦,
通过共享辅助方法消除了 4x 重复. 业务层不直接依赖, 经 LLMGateway 调度.
"""

from __future__ import annotations

from .chain_builder import build_dispatch_chain, build_utility_dispatch_chain
from .dispatcher import ChainEntry, LLMDispatcher

__all__ = [
    "ChainEntry",
    "LLMDispatcher",
    "build_dispatch_chain",
    "build_utility_dispatch_chain",
]
