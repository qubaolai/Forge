"""向后兼容重导出: 旧路径 forge.llm.fallback → forge.llm.dispatch.dispatcher.

新代码请使用 forge.llm.dispatch.dispatcher.LLMDispatcher.
LLMFallbackChain 作为 LLMDispatcher 的别名保留.
"""

from __future__ import annotations

from .dispatch.dispatcher import ChainEntry, LLMDispatcher

# 旧名字保留为别名
LLMFallbackChain = LLMDispatcher

__all__ = [
    "ChainEntry",
    "LLMDispatcher",
    "LLMFallbackChain",
]
