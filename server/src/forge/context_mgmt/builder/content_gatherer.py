"""ContentGatherer: 并行调用所有 ContentProvider, 独立处理每个失败.

设计:
    - asyncio.gather(return_exceptions=True) 让单 Provider 失败不影响其他.
    - 失败的 Provider 不写入 chunks 字典, 而是记录到返回的 degraded 列表,
      由 MessageAssembler 在生成 ContextSnapshot 时合并到 snapshot.degraded.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from forge.context_mgmt.protocols import ContentProvider, ContentProviderError
from forge.context_mgmt.types import ContentChunk, ContextRequest

logger = logging.getLogger(__name__)


@dataclass
class GatherResult:
    """ContentGatherer.gather 的返回值.

    chunks:    name -> list[ContentChunk] (成功的 Provider).
    degraded:  Provider 失败原因列表 (形如 ["summary_fetch_failed"]).
    """

    chunks: dict[str, list[ContentChunk]]
    degraded: list[str]


class ContentGatherer:
    """无状态, 内部并行调度 Provider."""

    def __init__(self, providers: list[ContentProvider]) -> None:
        self._providers = providers

    async def gather(self, request: ContextRequest) -> GatherResult:
        results = await asyncio.gather(
            *[p.provide(request) for p in self._providers],
            return_exceptions=True,
        )
        chunks: dict[str, list[ContentChunk]] = {}
        degraded: list[str] = []
        for provider, result in zip(self._providers, results, strict=False):
            if isinstance(result, ContentProviderError):
                # 软失败: Provider 显式声明可降级
                degraded.append(result.reason_code or f"{provider.name}_fetch_failed")
            elif isinstance(result, BaseException):
                # 硬失败: 未包装异常意味着 Provider 认为不可降级 (典型: HistoryProvider)
                # 直接向上抛, 让 DefaultContextBuilder 失败
                raise result
            else:
                chunks[provider.name] = result
        return GatherResult(chunks=chunks, degraded=degraded)
