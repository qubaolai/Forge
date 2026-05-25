"""CompositeContextBuilder: 旧 ContextBuilder Protocol 入口 (compat 层).

迁移说明 (2026-05):
    本类已改为 forge.context_mgmt.compat.CompositeContextBuilderAdapter 的别名,
    内部完全委托新 DefaultContextBuilder (Fork-Join 并行).

    对外接口 100% 兼容:
        - 构造函数签名: (history_repo, memory_store, token_counter)
        - 主入口: async def build(BuildRequest) -> AssembledContext

    新调用方应直接使用 forge.context_mgmt.ContextManager (含压缩 + 用量视图).
"""

from __future__ import annotations

from forge.context_mgmt.compat.context_builder_adapter import (
    CompositeContextBuilderAdapter as CompositeContextBuilder,
)

__all__ = ["CompositeContextBuilder"]
