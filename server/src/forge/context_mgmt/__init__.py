"""统一上下文管理系统 (Context Management System).

对外统一入口 ContextManager, 内部 Fork-Join 并行构建,
压缩子系统完全解耦可独立触发 (chat 路径).

子模块:
    - protocols:  所有扩展点 ABC
    - types:      统一值对象 (ContextRequest / ContextSnapshot / ...)
    - manager:    ContextManager 统一入口
    - builder/:   上下文构建 (Fork-Join 并行)
    - providers/: ContentProvider 实现
    - filters/:   HistoryFilter 实现
    - tool_policy/: ToolResultPolicy 实现
    - budget/:    BudgetPolicy + WindowBudget
    - meter/:     TokenMeter 包装
    - compaction/: 压缩子系统 (Trigger + Strategy + Controller)
    - guards/:    LoopGuard (从 chat/guards 物理移入)
"""

from forge.context_mgmt.manager import ContextManager
from forge.context_mgmt.protocols import (
    BudgetPolicy,
    CompactionError,
    CompactionStrategy,
    CompactionTrigger,
    ContentProvider,
    ContentProviderError,
    HistoryFilter,
    TokenMeter,
    ToolResultPolicy,
)
from forge.context_mgmt.types import (
    CompactionResult,
    ContentChunk,
    ContextRequest,
    ContextSnapshot,
    ContextUsage,
    HistoryMessage,
    LayerUsage,
    WindowBudget,
)

__all__ = [
    # 入口
    "ContextManager",
    # 类型
    "ContextRequest",
    "ContextSnapshot",
    "ContextUsage",
    "LayerUsage",
    "WindowBudget",
    "ContentChunk",
    "HistoryMessage",
    "CompactionResult",
    # ABC
    "TokenMeter",
    "BudgetPolicy",
    "ContentProvider",
    "HistoryFilter",
    "ToolResultPolicy",
    "CompactionTrigger",
    "CompactionStrategy",
    # 异常
    "ContentProviderError",
    "CompactionError",
]
