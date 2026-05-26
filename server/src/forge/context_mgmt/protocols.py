"""上下文管理系统的所有扩展点 Protocol.

设计原则:
    - 每个 Protocol 只暴露最小接口, 不强迫实现方承担不相关的职责.
    - async 为主 (IO 操作), 纯计算用 sync.
    - 失败语义统一: 业务失败抛特定异常 (ContentProviderError / CompactionError),
      调用方负责捕获降级.

扩展点清单:
    - TokenMeter:        token 计量 (包装 llm/token_counter.py)
    - BudgetPolicy:      WindowBudget 分配策略 (按 mode)
    - ContentProvider:   异步提供一类上下文素材 (history / summary / facts / workspace)
    - HistoryFilter:     决定哪些历史消息进入上下文 (recent / semantic / hybrid)
    - ToolResultPolicy:  决定工具结果如何出现在历史中 (truncate / evict / summarize)
    - CompactionTrigger: 压缩触发条件判断 (threshold / explicit / scheduled)
    - CompactionStrategy: 压缩执行 (summary / selective_drop / hybrid / null)
"""

from __future__ import annotations

from abc import ABC

from forge.context_mgmt.types import (
    CompactionResult,
    ContentChunk,
    ContextMode,
    ContextRequest,
    ContextSnapshot,
    HistoryMessage,
    WindowBudget,
)
from forge.core.types.message import Message


# ---------------------------------------------------------------------------
# 1. TokenMeter: 统一的 token 计量接口.
# ---------------------------------------------------------------------------
class TokenMeter(ABC):
    """线程 / 协程安全 (无可变状态)."""

    def count_text(self, text: str) -> int: ...

    def count_messages(self, messages: list[Message]) -> int:
        """估算一组消息的 prompt token 数 (含 role / 分隔符开销)."""
        ...


# ---------------------------------------------------------------------------
# 2. BudgetPolicy: 从 ContextRequest 计算 WindowBudget.
# ---------------------------------------------------------------------------
class BudgetPolicy(ABC):
    """无状态. 同一实例可并发使用."""

    def allocate(self, request: ContextRequest) -> WindowBudget: ...


# ---------------------------------------------------------------------------
# 3. ContentProvider: 异步提供一类上下文素材.
# ---------------------------------------------------------------------------
class ContentProvider(ABC):
    """每个 Provider 负责一类上下文素材.

    返回 ContentChunk 列表 —— 每个 chunk 携带 estimated_tokens,
    让 MessageAssembler 裁剪时不重复计数.

    失败语义:
        - IO 失败           -> raise ContentProviderError
        - 业务上空           -> 返回 [] (不是错误)
        - 该 Provider 不适用 -> 返回 [] (例如 SummaryProvider 在 enable_summary=False 时)
    """

    async def provide(self, request: ContextRequest) -> list[ContentChunk]: ...

    @property
    def name(self) -> str:
        """唯一标识. 失败时写入 snapshot.degraded 用此名."""
        ...


class ContentProviderError(Exception):
    """ContentProvider 的 IO 失败异常.

    ContentGatherer 捕获后写入 snapshot.degraded, 不向上抛.

    reason_code 由 Provider 自主选择, 出现在 snapshot.degraded 列表中.
    例如:
        - SummaryProvider 失败 -> ContentProviderError("summary_fetch_failed", ...)
        - FactsProvider 失败   -> ContentProviderError("facts_recall_failed", ...)
    若未指定, Gatherer 用 "{provider_name}_fetch_failed" 兜底.
    """

    def __init__(self, reason_code: str, *args) -> None:
        super().__init__(reason_code, *args)
        self.reason_code = reason_code


# ---------------------------------------------------------------------------
# 4. HistoryFilter: 决定哪些历史消息纳入上下文.
# ---------------------------------------------------------------------------
class HistoryFilter(ABC):
    """按相关性过滤历史消息.

    实现:
        - RecentFilter:   返回原列表 (兜底)
        - SemanticFilter: 按向量相似度过滤
        - HybridFilter:   近期锚点 + 语义过滤 (chat 默认)
        - NullFilter:     返回空 (task 模式不要历史)
        - StepScopedFilter: 只返回本 step 的消息 (workflow 模式)
    """

    async def filter(
        self,
        messages: list[HistoryMessage],
        query: str,
        mode: ContextMode,
    ) -> list[HistoryMessage]: ...


# ---------------------------------------------------------------------------
# 5. ToolResultPolicy: 决定工具调用结果如何出现在历史中.
# ---------------------------------------------------------------------------
class ToolResultPolicy(ABC):
    """处理跨轮加载回来的 tool_result content.

    实现:
        - VerbatimPolicy:    保持原样 (调试用)
        - TruncatingPolicy:  截断到 MAX tokens (chat 默认)
        - EvictingPolicy:    替换为占位符 (task 默认)
        - SummarizingPolicy: LLM 摘要 (workflow 默认)
    """

    def process(
        self,
        tool_name: str,
        original_content: str,
        token_budget: int,
        meter: TokenMeter,
    ) -> str:
        """返回处理后的内容 (截断 / 摘要文本 / 占位符)."""
        ...

    @property
    def name(self) -> str: ...


# ---------------------------------------------------------------------------
# 6. CompactionTrigger: 压缩触发条件 (与执行解耦).
# ---------------------------------------------------------------------------
class CompactionTrigger(ABC):
    """判断是否应触发压缩, 与执行逻辑无关."""

    def should_compact(self, snapshot: ContextSnapshot) -> bool: ...

    @property
    def name(self) -> str: ...


# ---------------------------------------------------------------------------
# 7. CompactionStrategy: 压缩执行 (与触发解耦).
# ---------------------------------------------------------------------------
class CompactionStrategy(ABC):
    """执行一次压缩.

    失败语义:
        - 基础设施失败 (DB / LLM) -> raise CompactionError
        - 业务上无需压缩 (如已没有可压缩内容) -> 返回 CompactionResult(success=False, failure_reason=...)
    """

    async def compact(
        self,
        session_id: str,
        snapshot: ContextSnapshot,
    ) -> CompactionResult: ...

    @property
    def name(self) -> str: ...


class CompactionError(Exception):
    """压缩失败 (LLM 调用错 / DB 写入错).

    CompactionController 捕获后写入 snapshot.degraded.
    """
