"""统一上下文管理系统 (Context Management System).

对外统一入口 ContextManager, 内部 Fork-Join 并行构建,
压缩子系统完全解耦可独立触发, 三种模式 (chat / task / workflow) 通过扩展点切换.

子模块:
    - protocols:  所有扩展点 Protocol
    - types:      统一值对象 (ContextRequest / ContextSnapshot / ...)
    - manager:    ContextManager 统一入口 + build_context_manager() 工厂
    - builder/:   上下文构建 (Fork-Join 并行)
    - providers/: ContentProvider 实现
    - filters/:   HistoryFilter 实现
    - tool_policy/: ToolResultPolicy 实现
    - budget/:    BudgetPolicy + WindowBudget
    - meter/:     TokenMeter 包装
    - compaction/: 压缩子系统 (Trigger + Strategy + Controller)
    - guards/:    LoopGuard (从 chat/guards 物理移入)
    - compat/:    渐进迁移兼容层

阶段 0: 仅 protocols + types 骨架. 后续阶段逐步填充.
"""

from forge.context_mgmt.manager import ContextManager, build_context_manager
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
    ContextMode,
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
    "build_context_manager",
    # 类型
    "ContextMode",
    "ContextRequest",
    "ContextSnapshot",
    "ContextUsage",
    "LayerUsage",
    "WindowBudget",
    "ContentChunk",
    "HistoryMessage",
    "CompactionResult",
    # Protocol
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
